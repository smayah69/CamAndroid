// CamAndroid virtual camera: a DirectShow live-source filter registered in the "Video Capture
// Sources" category, so any DirectShow/Chromium/Zoom/Teams/Discord/OBS app can select it.
// It runs inside the consuming application's process and reads frames that camandroid.exe
// publishes through shared memory (see shared_frame.h). No kernel driver is involved.
//
// Written without the DirectShow BaseClasses: one filter with one output pin, the pin
// enumerators, IAMStreamConfig for format selection and IKsPropertySet for the pin category.

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dshow.h>
#include <olectl.h>

#include <atomic>
#include <cstring>
#include <cwchar>
#include <new>
#include <vector>

#include "shared_frame.h"

#ifndef KSPROPERTY_SUPPORT_GET
#define KSPROPERTY_SUPPORT_GET 1
#endif

// {F216EFC3-E8A2-425E-A80C-24FBA2B0DB23}
static const GUID CLSID_CamAndroidVCam = {
    0xf216efc3, 0xe8a2, 0x425e, {0xa8, 0x0c, 0x24, 0xfb, 0xa2, 0xb0, 0xdb, 0x23}};
static const wchar_t kClsidString[] = L"{F216EFC3-E8A2-425E-A80C-24FBA2B0DB23}";
static const wchar_t kCategoryString[] = L"{860BB310-5D01-11D0-BD3B-00A0C911CE86}";  // CLSID_VideoInputDeviceCategory
static const wchar_t kFriendlyName[] = L"CamAndroid";
static const wchar_t kPinName[] = L"Capture";

static HMODULE g_module = nullptr;

static void CopyName(wchar_t* dst, size_t capacity, const wchar_t* src) {
    size_t i = 0;
    for (; i + 1 < capacity && src[i]; ++i) dst[i] = src[i];
    dst[i] = 0;
}
static std::atomic<long> g_objects{0};
static std::atomic<long> g_locks{0};

// ---------------------------------------------------------------------------------------------
// Media types
// ---------------------------------------------------------------------------------------------

struct VideoFormat {
    int width;
    int height;
    REFERENCE_TIME frameInterval;  // 100 ns units
    bool topDown;
};

static const int kSizes[][2] = {{1280, 720}, {1920, 1080}, {960, 540}, {640, 480}, {640, 360}, {320, 240}};
static const int kNumSizes = sizeof(kSizes) / sizeof(kSizes[0]);
static const REFERENCE_TIME kDefaultInterval = 10000000 / 30;
static const REFERENCE_TIME kMinInterval = 10000000 / 60;
static const REFERENCE_TIME kMaxInterval = 10000000 / 5;

static int StrideOf(int width) { return (width * 3 + 3) & ~3; }

static void FreeMediaType(AM_MEDIA_TYPE& mt) {
    if (mt.cbFormat != 0) CoTaskMemFree(mt.pbFormat);
    mt.cbFormat = 0;
    mt.pbFormat = nullptr;
    if (mt.pUnk) {
        mt.pUnk->Release();
        mt.pUnk = nullptr;
    }
}

static HRESULT CopyMediaType(AM_MEDIA_TYPE* dst, const AM_MEDIA_TYPE* src) {
    *dst = *src;
    if (src->cbFormat != 0) {
        dst->pbFormat = static_cast<BYTE*>(CoTaskMemAlloc(src->cbFormat));
        if (!dst->pbFormat) {
            dst->cbFormat = 0;
            return E_OUTOFMEMORY;
        }
        memcpy(dst->pbFormat, src->pbFormat, src->cbFormat);
    }
    if (dst->pUnk) dst->pUnk->AddRef();
    return S_OK;
}

static HRESULT FillMediaType(const VideoFormat& f, AM_MEDIA_TYPE* mt) {
    ZeroMemory(mt, sizeof(*mt));
    auto* vih = static_cast<VIDEOINFOHEADER*>(CoTaskMemAlloc(sizeof(VIDEOINFOHEADER)));
    if (!vih) return E_OUTOFMEMORY;
    ZeroMemory(vih, sizeof(*vih));
    const DWORD imageSize = static_cast<DWORD>(StrideOf(f.width)) * f.height;
    vih->AvgTimePerFrame = f.frameInterval;
    vih->dwBitRate = static_cast<DWORD>(static_cast<double>(imageSize) * 8 * 10000000.0 / f.frameInterval);
    vih->bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    vih->bmiHeader.biWidth = f.width;
    vih->bmiHeader.biHeight = f.topDown ? -f.height : f.height;
    vih->bmiHeader.biPlanes = 1;
    vih->bmiHeader.biBitCount = 24;
    vih->bmiHeader.biCompression = BI_RGB;
    vih->bmiHeader.biSizeImage = imageSize;
    mt->majortype = MEDIATYPE_Video;
    mt->subtype = MEDIASUBTYPE_RGB24;
    mt->formattype = FORMAT_VideoInfo;
    mt->bFixedSizeSamples = TRUE;
    mt->bTemporalCompression = FALSE;
    mt->lSampleSize = imageSize;
    mt->cbFormat = sizeof(VIDEOINFOHEADER);
    mt->pbFormat = reinterpret_cast<BYTE*>(vih);
    return S_OK;
}

static AM_MEDIA_TYPE* CreateMediaType(const VideoFormat& f) {
    auto* mt = static_cast<AM_MEDIA_TYPE*>(CoTaskMemAlloc(sizeof(AM_MEDIA_TYPE)));
    if (!mt) return nullptr;
    if (FAILED(FillMediaType(f, mt))) {
        CoTaskMemFree(mt);
        return nullptr;
    }
    return mt;
}

/** Accepts RGB24 VIDEOINFOHEADER types of any reasonable size; the frame is scaled to fit. */
static bool ParseMediaType(const AM_MEDIA_TYPE* mt, VideoFormat* out) {
    if (!mt || mt->majortype != MEDIATYPE_Video || mt->subtype != MEDIASUBTYPE_RGB24) return false;
    if (mt->formattype != FORMAT_VideoInfo || mt->cbFormat < sizeof(VIDEOINFOHEADER) || !mt->pbFormat) return false;
    const auto* vih = reinterpret_cast<const VIDEOINFOHEADER*>(mt->pbFormat);
    const BITMAPINFOHEADER& bih = vih->bmiHeader;
    const int h = bih.biHeight < 0 ? -bih.biHeight : bih.biHeight;
    if (bih.biBitCount != 24 || bih.biCompression != BI_RGB) return false;
    if (bih.biWidth < 16 || bih.biWidth > (LONG)VCAM_MAX_WIDTH || h < 16 || h > (LONG)VCAM_MAX_HEIGHT) return false;
    VideoFormat f;
    f.width = bih.biWidth;
    f.height = h;
    f.topDown = bih.biHeight < 0;
    f.frameInterval = vih->AvgTimePerFrame;
    if (f.frameInterval < kMinInterval || f.frameInterval > kMaxInterval) f.frameInterval = kDefaultInterval;
    if (out) *out = f;
    return true;
}

// ---------------------------------------------------------------------------------------------
// Frame source: shared memory written by camandroid.exe, or a "waiting" picture.
// ---------------------------------------------------------------------------------------------

static uint64_t NowUnixMs() {
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    ULARGE_INTEGER t;
    t.LowPart = ft.dwLowDateTime;
    t.HighPart = ft.dwHighDateTime;
    return (t.QuadPart - 116444736000000000ULL) / 10000ULL;
}

class FrameReader {
public:
    ~FrameReader() { Close(); }

    /** Copies the latest frame into `rgb` (RGB24 top-down). Returns false if none is fresh. */
    bool Read(std::vector<uint8_t>& rgb, int& width, int& height) {
        if (!view_ && !Open()) return false;
        const auto* hdr = reinterpret_cast<const VCamHeader*>(view_);
        if (hdr->magic != VCAM_MAGIC || hdr->version != VCAM_VERSION) return false;
        for (int attempt = 0; attempt < 4; ++attempt) {
            const uint32_t s1 = hdr->seq;
            MemoryBarrier();
            if (s1 & 1u) {
                Sleep(1);
                continue;
            }
            const uint32_t w = hdr->width, h = hdr->height;
            const uint64_t ts = hdr->timestamp_ms;
            if (w == 0 || h == 0 || w > VCAM_MAX_WIDTH || h > VCAM_MAX_HEIGHT) return false;
            const uint64_t now = NowUnixMs();
            if (now > ts + VCAM_STALE_MS) return false;
            const size_t bytes = static_cast<size_t>(w) * h * 3;
            if (sizeof(VCamHeader) + bytes > viewSize_) return false;
            rgb.resize(bytes);
            memcpy(rgb.data(), view_ + sizeof(VCamHeader), bytes);
            MemoryBarrier();
            if (hdr->seq == s1) {
                width = static_cast<int>(w);
                height = static_cast<int>(h);
                return true;
            }
        }
        return false;
    }

private:
    bool Open() {
        const ULONGLONG now = GetTickCount64();
        if (now - lastAttempt_ < 500) return false;
        lastAttempt_ = now;
        map_ = OpenFileMappingW(FILE_MAP_READ, FALSE, VCAM_MAPPING_NAME);
        if (!map_) return false;
        view_ = static_cast<const uint8_t*>(MapViewOfFile(map_, FILE_MAP_READ, 0, 0, 0));
        if (!view_) {
            Close();
            return false;
        }
        MEMORY_BASIC_INFORMATION mbi;
        viewSize_ = VirtualQuery(view_, &mbi, sizeof(mbi)) ? mbi.RegionSize : 0;
        if (viewSize_ < sizeof(VCamHeader)) {
            Close();
            return false;
        }
        return true;
    }

    void Close() {
        if (view_) UnmapViewOfFile(view_);
        if (map_) CloseHandle(map_);
        view_ = nullptr;
        map_ = nullptr;
        viewSize_ = 0;
    }

    HANDLE map_ = nullptr;
    const uint8_t* view_ = nullptr;
    size_t viewSize_ = 0;
    ULONGLONG lastAttempt_ = 0;
};

/** Renders "waiting for the phone" into a bottom-up BGR24 DIB of the given size. */
static std::vector<uint8_t> RenderPlaceholder(int width, int height) {
    const int stride = StrideOf(width);
    std::vector<uint8_t> out(static_cast<size_t>(stride) * height, 0);
    BITMAPINFO bmi = {};
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = height;
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 24;
    bmi.bmiHeader.biCompression = BI_RGB;
    void* bits = nullptr;
    HDC dc = CreateCompatibleDC(nullptr);
    HBITMAP bmp = dc ? CreateDIBSection(dc, &bmi, DIB_RGB_COLORS, &bits, nullptr, 0) : nullptr;
    if (bmp && bits) {
        HGDIOBJ old = SelectObject(dc, bmp);
        RECT rc = {0, 0, width, height};
        HBRUSH bg = CreateSolidBrush(RGB(0x1F, 0x2A, 0x44));
        FillRect(dc, &rc, bg);
        DeleteObject(bg);
        HFONT font = CreateFontW(-(height / 14), 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE, DEFAULT_CHARSET,
                                 OUT_DEFAULT_PRECIS, CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, L"Segoe UI");
        HGDIOBJ oldFont = SelectObject(dc, font);
        SetBkMode(dc, TRANSPARENT);
        SetTextColor(dc, RGB(0xFF, 0xFF, 0xFF));
        RECT top = {0, 0, width, height / 2};
        DrawTextW(dc, L"CamAndroid", -1, &top, DT_CENTER | DT_BOTTOM | DT_SINGLELINE);
        SetTextColor(dc, RGB(0x9F, 0xB3, 0xD9));
        RECT bottom = {0, height / 2 + height / 40, width, height};
        DrawTextW(dc, L"Waiting for the phone\x2026", -1, &bottom, DT_CENTER | DT_TOP | DT_SINGLELINE);
        SelectObject(dc, oldFont);
        DeleteObject(font);
        GdiFlush();
        memcpy(out.data(), bits, out.size());
        SelectObject(dc, old);
    }
    if (bmp) DeleteObject(bmp);
    if (dc) DeleteDC(dc);
    return out;
}

/**
 * Scales an RGB24 top-down source into a BGR24 DIB (letterboxed, nearest neighbour).
 * `topDown` describes the destination row order.
 */
static void BlitFrame(const uint8_t* src, int sw, int sh, uint8_t* dst, int dw, int dh, bool topDown) {
    const int stride = StrideOf(dw);
    memset(dst, 0, static_cast<size_t>(stride) * dh);
    // Fit preserving aspect ratio.
    int fw = dw, fh = static_cast<int>(static_cast<int64_t>(sh) * dw / sw);
    if (fh > dh) {
        fh = dh;
        fw = static_cast<int>(static_cast<int64_t>(sw) * dh / sh);
    }
    if (fw <= 0 || fh <= 0) return;
    const int ox = (dw - fw) / 2, oy = (dh - fh) / 2;
    std::vector<int> xmap(fw);
    for (int x = 0; x < fw; ++x) xmap[x] = static_cast<int>(static_cast<int64_t>(x) * sw / fw) * 3;
    for (int y = 0; y < fh; ++y) {
        const int sy = static_cast<int>(static_cast<int64_t>(y) * sh / fh);
        const uint8_t* srow = src + static_cast<size_t>(sy) * sw * 3;
        const int outY = oy + y;
        uint8_t* drow = dst + static_cast<size_t>(topDown ? outY : (dh - 1 - outY)) * stride + ox * 3;
        for (int x = 0; x < fw; ++x) {
            const uint8_t* p = srow + xmap[x];
            drow[0] = p[2];
            drow[1] = p[1];
            drow[2] = p[0];
            drow += 3;
        }
    }
}

// ---------------------------------------------------------------------------------------------
// Filter and pin
// ---------------------------------------------------------------------------------------------

class VCamFilter;

class VCamPin final : public IPin, public IAMStreamConfig, public IKsPropertySet {
public:
    explicit VCamPin(VCamFilter* filter) : filter_(filter) { ZeroMemory(&mt_, sizeof(mt_)); }
    ~VCamPin() {
        StopStreaming();
        SafeRelease(allocator_);
        SafeRelease(input_);
        SafeRelease(connected_);
        FreeMediaType(mt_);
    }

    // IUnknown: lifetime is tied to the owning filter.
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override;
    STDMETHODIMP_(ULONG) AddRef() override;
    STDMETHODIMP_(ULONG) Release() override;

    // IPin
    STDMETHODIMP Connect(IPin* receivePin, const AM_MEDIA_TYPE* pmt) override;
    STDMETHODIMP ReceiveConnection(IPin*, const AM_MEDIA_TYPE*) override { return E_UNEXPECTED; }
    STDMETHODIMP Disconnect() override;
    STDMETHODIMP ConnectedTo(IPin** pin) override;
    STDMETHODIMP ConnectionMediaType(AM_MEDIA_TYPE* pmt) override;
    STDMETHODIMP QueryPinInfo(PIN_INFO* info) override;
    STDMETHODIMP QueryDirection(PIN_DIRECTION* dir) override {
        if (!dir) return E_POINTER;
        *dir = PINDIR_OUTPUT;
        return S_OK;
    }
    STDMETHODIMP QueryId(LPWSTR* id) override;
    STDMETHODIMP QueryAccept(const AM_MEDIA_TYPE* pmt) override { return ParseMediaType(pmt, nullptr) ? S_OK : S_FALSE; }
    STDMETHODIMP EnumMediaTypes(IEnumMediaTypes** ppEnum) override;
    STDMETHODIMP QueryInternalConnections(IPin**, ULONG*) override { return E_NOTIMPL; }
    STDMETHODIMP EndOfStream() override { return E_UNEXPECTED; }
    STDMETHODIMP BeginFlush() override { return E_UNEXPECTED; }
    STDMETHODIMP EndFlush() override { return E_UNEXPECTED; }
    STDMETHODIMP NewSegment(REFERENCE_TIME, REFERENCE_TIME, double) override { return S_OK; }

    // IAMStreamConfig
    STDMETHODIMP SetFormat(AM_MEDIA_TYPE* pmt) override;
    STDMETHODIMP GetFormat(AM_MEDIA_TYPE** ppmt) override;
    STDMETHODIMP GetNumberOfCapabilities(int* count, int* size) override;
    STDMETHODIMP GetStreamCaps(int index, AM_MEDIA_TYPE** ppmt, BYTE* caps) override;

    // IKsPropertySet
    STDMETHODIMP Set(REFGUID, DWORD, LPVOID, DWORD, LPVOID, DWORD) override { return E_NOTIMPL; }
    STDMETHODIMP Get(REFGUID set, DWORD id, LPVOID, DWORD, LPVOID data, DWORD cb, DWORD* returned) override;
    STDMETHODIMP QuerySupported(REFGUID set, DWORD id, DWORD* support) override;

    // Called by the filter.
    HRESULT Active();    // Stopped -> Paused
    void Inactive();     // -> Stopped
    bool IsConnected() const { return connected_ != nullptr; }

    /** Formats offered in enumeration: the SetFormat() choice, or the default list. */
    std::vector<VideoFormat> OfferedFormats();

private:
    template <class T>
    static void SafeRelease(T*& p) {
        if (p) {
            p->Release();
            p = nullptr;
        }
    }
    HRESULT TryConnect(IPin* receivePin, const AM_MEDIA_TYPE* mt);
    HRESULT DecideAllocator();
    void StopStreaming();
    static DWORD WINAPI ThreadProc(LPVOID self);
    void StreamLoop();
    void FillSample(BYTE* data, const VideoFormat& f);

    VCamFilter* filter_;
    IPin* connected_ = nullptr;
    IMemInputPin* input_ = nullptr;
    IMemAllocator* allocator_ = nullptr;
    AM_MEDIA_TYPE mt_;
    VideoFormat format_{};
    bool hasPreferred_ = false;
    VideoFormat preferred_{};

    HANDLE thread_ = nullptr;
    HANDLE stopEvent_ = nullptr;

    // Streaming-thread state.
    FrameReader reader_;
    std::vector<uint8_t> rgb_;
    std::vector<uint8_t> placeholder_;
    int placeholderW_ = 0, placeholderH_ = 0;
};

class VCamFilter final : public IBaseFilter, public IAMFilterMiscFlags {
public:
    VCamFilter() : pin_(this) {
        InitializeCriticalSection(&lock_);
        name_[0] = 0;
        ++g_objects;
    }
    ~VCamFilter() {
        pin_.Inactive();
        if (clock_) clock_->Release();
        DeleteCriticalSection(&lock_);
        --g_objects;
    }

    // IUnknown
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IPersist || riid == IID_IMediaFilter || riid == IID_IBaseFilter) {
            *ppv = static_cast<IBaseFilter*>(this);
        } else if (riid == IID_IAMFilterMiscFlags) {
            *ppv = static_cast<IAMFilterMiscFlags*>(this);
        } else if (riid == IID_IAMStreamConfig || riid == IID_IKsPropertySet) {
            // Some apps look for these on the filter rather than the pin.
            return pin_.QueryInterface(riid, ppv);
        } else {
            *ppv = nullptr;
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        const LONG r = InterlockedDecrement(&refs_);
        if (r == 0) delete this;
        return r;
    }

    // IPersist
    STDMETHODIMP GetClassID(CLSID* clsid) override {
        if (!clsid) return E_POINTER;
        *clsid = CLSID_CamAndroidVCam;
        return S_OK;
    }

    // IMediaFilter
    STDMETHODIMP Stop() override {
        EnterCriticalSection(&lock_);
        const bool wasActive = state_ != State_Stopped;
        state_ = State_Stopped;
        LeaveCriticalSection(&lock_);
        if (wasActive) pin_.Inactive();
        return S_OK;
    }
    STDMETHODIMP Pause() override {
        EnterCriticalSection(&lock_);
        HRESULT hr = S_OK;
        if (state_ == State_Stopped && pin_.IsConnected()) hr = pin_.Active();
        if (SUCCEEDED(hr)) state_ = State_Paused;
        LeaveCriticalSection(&lock_);
        return hr;
    }
    STDMETHODIMP Run(REFERENCE_TIME start) override {
        EnterCriticalSection(&lock_);
        HRESULT hr = S_OK;
        if (state_ == State_Stopped && pin_.IsConnected()) hr = pin_.Active();
        if (SUCCEEDED(hr)) {
            start_ = start;
            runStartQpc_ = QpcNow();
            state_ = State_Running;
        }
        LeaveCriticalSection(&lock_);
        return hr;
    }
    STDMETHODIMP GetState(DWORD, FILTER_STATE* state) override {
        if (!state) return E_POINTER;
        *state = state_;
        // A live source produces no data while paused.
        return state_ == State_Paused ? VFW_S_CANT_CUE : S_OK;
    }
    STDMETHODIMP SetSyncSource(IReferenceClock* clock) override {
        EnterCriticalSection(&lock_);
        if (clock) clock->AddRef();
        if (clock_) clock_->Release();
        clock_ = clock;
        LeaveCriticalSection(&lock_);
        return S_OK;
    }
    STDMETHODIMP GetSyncSource(IReferenceClock** clock) override {
        if (!clock) return E_POINTER;
        EnterCriticalSection(&lock_);
        *clock = clock_;
        if (clock_) clock_->AddRef();
        LeaveCriticalSection(&lock_);
        return S_OK;
    }

    // IBaseFilter
    STDMETHODIMP EnumPins(IEnumPins** ppEnum) override;
    STDMETHODIMP FindPin(LPCWSTR id, IPin** pin) override {
        if (!id || !pin) return E_POINTER;
        if (wcscmp(id, kPinName) != 0) {
            *pin = nullptr;
            return VFW_E_NOT_FOUND;
        }
        *pin = &pin_;
        pin_.AddRef();
        return S_OK;
    }
    STDMETHODIMP QueryFilterInfo(FILTER_INFO* info) override {
        if (!info) return E_POINTER;
        CopyName(info->achName, MAX_FILTER_NAME, name_[0] ? name_ : kFriendlyName);
        info->pGraph = graph_;
        if (graph_) graph_->AddRef();
        return S_OK;
    }
    STDMETHODIMP JoinFilterGraph(IFilterGraph* graph, LPCWSTR name) override {
        // Per DirectShow rules the filter keeps a weak (non-AddRef'd) pointer to its graph.
        EnterCriticalSection(&lock_);
        graph_ = graph;
        if (name)
            CopyName(name_, MAX_FILTER_NAME, name);
        else
            name_[0] = 0;
        LeaveCriticalSection(&lock_);
        return S_OK;
    }
    STDMETHODIMP QueryVendorInfo(LPWSTR* info) override {
        if (!info) return E_POINTER;
        const size_t bytes = sizeof(L"CamAndroid");
        *info = static_cast<LPWSTR>(CoTaskMemAlloc(bytes));
        if (!*info) return E_OUTOFMEMORY;
        memcpy(*info, L"CamAndroid", bytes);
        return S_OK;
    }

    // IAMFilterMiscFlags
    STDMETHODIMP_(ULONG) GetMiscFlags() override { return AM_FILTER_MISC_FLAGS_IS_SOURCE; }

    /** Current stream time (100 ns since Run). */
    REFERENCE_TIME StreamTime() {
        IReferenceClock* clock = nullptr;
        REFERENCE_TIME start;
        LONGLONG qpcStart;
        EnterCriticalSection(&lock_);
        clock = clock_;
        if (clock) clock->AddRef();
        start = start_;
        qpcStart = runStartQpc_;
        LeaveCriticalSection(&lock_);
        REFERENCE_TIME now = 0;
        if (clock && SUCCEEDED(clock->GetTime(&now))) {
            clock->Release();
            return now - start;
        }
        if (clock) clock->Release();
        return QpcToRefTime(QpcNow() - qpcStart);
    }

    FILTER_STATE State() const { return state_; }
    IFilterGraph* Graph() const { return graph_; }

private:
    static LONGLONG QpcNow() {
        LARGE_INTEGER v;
        QueryPerformanceCounter(&v);
        return v.QuadPart;
    }
    static REFERENCE_TIME QpcToRefTime(LONGLONG ticks) {
        LARGE_INTEGER f;
        QueryPerformanceFrequency(&f);
        return static_cast<REFERENCE_TIME>(static_cast<double>(ticks) * 10000000.0 / f.QuadPart);
    }

    volatile LONG refs_ = 1;
    CRITICAL_SECTION lock_;
    volatile FILTER_STATE state_ = State_Stopped;
    REFERENCE_TIME start_ = 0;
    LONGLONG runStartQpc_ = 0;
    IReferenceClock* clock_ = nullptr;
    IFilterGraph* graph_ = nullptr;
    WCHAR name_[MAX_FILTER_NAME];
    VCamPin pin_;

    friend class VCamPin;
};

// ---------------------------------------------------------------------------------------------
// Enumerators
// ---------------------------------------------------------------------------------------------

class EnumPinsImpl final : public IEnumPins {
public:
    EnumPinsImpl(VCamFilter* filter, IPin* pin, ULONG pos) : filter_(filter), pin_(pin), pos_(pos) {
        filter_->AddRef();
        ++g_objects;
    }
    ~EnumPinsImpl() {
        filter_->Release();
        --g_objects;
    }
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IEnumPins) {
            *ppv = static_cast<IEnumPins*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        const LONG r = InterlockedDecrement(&refs_);
        if (r == 0) delete this;
        return r;
    }
    STDMETHODIMP Next(ULONG count, IPin** pins, ULONG* fetched) override {
        if (!pins) return E_POINTER;
        if (count > 1 && !fetched) return E_INVALIDARG;
        ULONG n = 0;
        if (count > 0 && pos_ == 0) {
            pins[0] = pin_;
            pin_->AddRef();
            pos_ = 1;
            n = 1;
        }
        if (fetched) *fetched = n;
        return n == count ? S_OK : S_FALSE;
    }
    STDMETHODIMP Skip(ULONG count) override {
        if (pos_ + count > 1) {
            pos_ = 1;
            return S_FALSE;
        }
        pos_ += count;
        return S_OK;
    }
    STDMETHODIMP Reset() override {
        pos_ = 0;
        return S_OK;
    }
    STDMETHODIMP Clone(IEnumPins** ppEnum) override {
        if (!ppEnum) return E_POINTER;
        *ppEnum = new (std::nothrow) EnumPinsImpl(filter_, pin_, pos_);
        return *ppEnum ? S_OK : E_OUTOFMEMORY;
    }

private:
    volatile LONG refs_ = 1;
    VCamFilter* filter_;
    IPin* pin_;
    ULONG pos_;
};

class EnumMediaTypesImpl final : public IEnumMediaTypes {
public:
    EnumMediaTypesImpl(std::vector<VideoFormat> formats, ULONG pos) : formats_(std::move(formats)), pos_(pos) {
        ++g_objects;
    }
    ~EnumMediaTypesImpl() { --g_objects; }
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IEnumMediaTypes) {
            *ppv = static_cast<IEnumMediaTypes*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    STDMETHODIMP_(ULONG) Release() override {
        const LONG r = InterlockedDecrement(&refs_);
        if (r == 0) delete this;
        return r;
    }
    STDMETHODIMP Next(ULONG count, AM_MEDIA_TYPE** types, ULONG* fetched) override {
        if (!types) return E_POINTER;
        if (count > 1 && !fetched) return E_INVALIDARG;
        ULONG n = 0;
        while (n < count && pos_ < formats_.size()) {
            types[n] = CreateMediaType(formats_[pos_]);
            if (!types[n]) break;
            ++n;
            ++pos_;
        }
        if (fetched) *fetched = n;
        return n == count ? S_OK : S_FALSE;
    }
    STDMETHODIMP Skip(ULONG count) override {
        pos_ += count;
        if (pos_ > formats_.size()) {
            pos_ = static_cast<ULONG>(formats_.size());
            return S_FALSE;
        }
        return S_OK;
    }
    STDMETHODIMP Reset() override {
        pos_ = 0;
        return S_OK;
    }
    STDMETHODIMP Clone(IEnumMediaTypes** ppEnum) override {
        if (!ppEnum) return E_POINTER;
        *ppEnum = new (std::nothrow) EnumMediaTypesImpl(formats_, pos_);
        return *ppEnum ? S_OK : E_OUTOFMEMORY;
    }

private:
    volatile LONG refs_ = 1;
    std::vector<VideoFormat> formats_;
    ULONG pos_;
};

STDMETHODIMP VCamFilter::EnumPins(IEnumPins** ppEnum) {
    if (!ppEnum) return E_POINTER;
    *ppEnum = new (std::nothrow) EnumPinsImpl(this, &pin_, 0);
    return *ppEnum ? S_OK : E_OUTOFMEMORY;
}

// ---------------------------------------------------------------------------------------------
// Pin implementation
// ---------------------------------------------------------------------------------------------

STDMETHODIMP VCamPin::QueryInterface(REFIID riid, void** ppv) {
    if (!ppv) return E_POINTER;
    if (riid == IID_IUnknown || riid == IID_IPin) {
        *ppv = static_cast<IPin*>(this);
    } else if (riid == IID_IAMStreamConfig) {
        *ppv = static_cast<IAMStreamConfig*>(this);
    } else if (riid == IID_IKsPropertySet) {
        *ppv = static_cast<IKsPropertySet*>(this);
    } else {
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
}

STDMETHODIMP_(ULONG) VCamPin::AddRef() { return filter_->AddRef(); }
STDMETHODIMP_(ULONG) VCamPin::Release() { return filter_->Release(); }

std::vector<VideoFormat> VCamPin::OfferedFormats() {
    std::vector<VideoFormat> list;
    if (hasPreferred_) {
        list.push_back(preferred_);
        return list;
    }
    for (int i = 0; i < kNumSizes; ++i) list.push_back({kSizes[i][0], kSizes[i][1], kDefaultInterval, false});
    return list;
}

STDMETHODIMP VCamPin::Connect(IPin* receivePin, const AM_MEDIA_TYPE* pmt) {
    if (!receivePin) return E_POINTER;
    EnterCriticalSection(&filter_->lock_);
    HRESULT hr;
    if (connected_) {
        hr = VFW_E_ALREADY_CONNECTED;
    } else if (filter_->state_ != State_Stopped) {
        hr = VFW_E_NOT_STOPPED;
    } else if (pmt && pmt->majortype != GUID_NULL && pmt->formattype != GUID_NULL && pmt->pbFormat) {
        // Fully specified type: try exactly that.
        hr = ParseMediaType(pmt, nullptr) ? TryConnect(receivePin, pmt) : VFW_E_TYPE_NOT_ACCEPTED;
    } else {
        hr = VFW_E_NO_ACCEPTABLE_TYPES;
        for (const VideoFormat& f : OfferedFormats()) {
            AM_MEDIA_TYPE mt;
            if (FAILED(FillMediaType(f, &mt))) continue;
            const bool matches = !pmt || ((pmt->majortype == GUID_NULL || pmt->majortype == mt.majortype) &&
                                          (pmt->subtype == GUID_NULL || pmt->subtype == mt.subtype));
            if (matches && SUCCEEDED(TryConnect(receivePin, &mt))) hr = S_OK;
            FreeMediaType(mt);
            if (hr == S_OK) break;
        }
    }
    LeaveCriticalSection(&filter_->lock_);
    return hr;
}

HRESULT VCamPin::TryConnect(IPin* receivePin, const AM_MEDIA_TYPE* mt) {
    VideoFormat f;
    if (!ParseMediaType(mt, &f)) return VFW_E_TYPE_NOT_ACCEPTED;
    HRESULT hr = receivePin->ReceiveConnection(this, mt);
    if (FAILED(hr)) return hr;
    connected_ = receivePin;
    connected_->AddRef();
    FreeMediaType(mt_);
    CopyMediaType(&mt_, mt);
    format_ = f;
    hr = connected_->QueryInterface(IID_IMemInputPin, reinterpret_cast<void**>(&input_));
    if (SUCCEEDED(hr)) hr = DecideAllocator();
    if (FAILED(hr)) {
        receivePin->Disconnect();
        SafeRelease(allocator_);
        SafeRelease(input_);
        SafeRelease(connected_);
        FreeMediaType(mt_);
    }
    return hr;
}

HRESULT VCamPin::DecideAllocator() {
    ALLOCATOR_PROPERTIES req = {};
    input_->GetAllocatorRequirements(&req);  // optional; failure leaves zeros
    ALLOCATOR_PROPERTIES props = {};
    props.cBuffers = req.cBuffers > 2 ? req.cBuffers : 2;
    props.cbBuffer = static_cast<long>(StrideOf(format_.width)) * format_.height;
    props.cbAlign = req.cbAlign > 0 ? req.cbAlign : 1;
    props.cbPrefix = req.cbPrefix;

    for (int attempt = 0; attempt < 2; ++attempt) {
        IMemAllocator* alloc = nullptr;
        HRESULT hr = attempt == 0 ? input_->GetAllocator(&alloc)
                                  : CoCreateInstance(CLSID_MemoryAllocator, nullptr, CLSCTX_INPROC_SERVER,
                                                     IID_IMemAllocator, reinterpret_cast<void**>(&alloc));
        if (FAILED(hr) || !alloc) continue;
        ALLOCATOR_PROPERTIES actual = {};
        hr = alloc->SetProperties(&props, &actual);
        if (SUCCEEDED(hr) && actual.cbBuffer >= props.cbBuffer) hr = input_->NotifyAllocator(alloc, FALSE);
        else if (SUCCEEDED(hr)) hr = E_FAIL;
        if (SUCCEEDED(hr)) {
            allocator_ = alloc;
            return S_OK;
        }
        alloc->Release();
    }
    return VFW_E_NO_ALLOCATOR;
}

STDMETHODIMP VCamPin::Disconnect() {
    EnterCriticalSection(&filter_->lock_);
    HRESULT hr = S_OK;
    if (!connected_) {
        hr = S_FALSE;
    } else if (filter_->state_ != State_Stopped) {
        hr = VFW_E_NOT_STOPPED;
    } else {
        if (allocator_) allocator_->Decommit();
        SafeRelease(allocator_);
        SafeRelease(input_);
        SafeRelease(connected_);
        FreeMediaType(mt_);
    }
    LeaveCriticalSection(&filter_->lock_);
    return hr;
}

STDMETHODIMP VCamPin::ConnectedTo(IPin** pin) {
    if (!pin) return E_POINTER;
    EnterCriticalSection(&filter_->lock_);
    *pin = connected_;
    if (connected_) connected_->AddRef();
    LeaveCriticalSection(&filter_->lock_);
    return *pin ? S_OK : VFW_E_NOT_CONNECTED;
}

STDMETHODIMP VCamPin::ConnectionMediaType(AM_MEDIA_TYPE* pmt) {
    if (!pmt) return E_POINTER;
    EnterCriticalSection(&filter_->lock_);
    HRESULT hr;
    if (connected_) {
        hr = CopyMediaType(pmt, &mt_);
    } else {
        ZeroMemory(pmt, sizeof(*pmt));
        hr = VFW_E_NOT_CONNECTED;
    }
    LeaveCriticalSection(&filter_->lock_);
    return hr;
}

STDMETHODIMP VCamPin::QueryPinInfo(PIN_INFO* info) {
    if (!info) return E_POINTER;
    info->pFilter = filter_;
    filter_->AddRef();
    info->dir = PINDIR_OUTPUT;
    CopyName(info->achName, MAX_PIN_NAME, kPinName);
    return S_OK;
}

STDMETHODIMP VCamPin::QueryId(LPWSTR* id) {
    if (!id) return E_POINTER;
    *id = static_cast<LPWSTR>(CoTaskMemAlloc(sizeof(kPinName)));
    if (!*id) return E_OUTOFMEMORY;
    memcpy(*id, kPinName, sizeof(kPinName));
    return S_OK;
}

STDMETHODIMP VCamPin::EnumMediaTypes(IEnumMediaTypes** ppEnum) {
    if (!ppEnum) return E_POINTER;
    EnterCriticalSection(&filter_->lock_);
    *ppEnum = new (std::nothrow) EnumMediaTypesImpl(OfferedFormats(), 0);
    LeaveCriticalSection(&filter_->lock_);
    return *ppEnum ? S_OK : E_OUTOFMEMORY;
}

STDMETHODIMP VCamPin::SetFormat(AM_MEDIA_TYPE* pmt) {
    EnterCriticalSection(&filter_->lock_);
    HRESULT hr = S_OK;
    VideoFormat f;
    if (filter_->state_ != State_Stopped) {
        hr = VFW_E_NOT_STOPPED;
    } else if (!pmt) {
        hasPreferred_ = false;
    } else if (!ParseMediaType(pmt, &f)) {
        hr = VFW_E_INVALIDMEDIATYPE;
    } else {
        preferred_ = f;
        hasPreferred_ = true;
    }
    IPin* reconnect = (SUCCEEDED(hr) && pmt && connected_) ? connected_ : nullptr;
    IFilterGraph* graph = filter_->graph_;
    LeaveCriticalSection(&filter_->lock_);
    // Already connected with another format: ask the graph to renegotiate.
    if (reconnect && graph) {
        AM_MEDIA_TYPE current;
        if (SUCCEEDED(ConnectionMediaType(&current))) {
            VideoFormat cur;
            const bool same = ParseMediaType(&current, &cur) && cur.width == f.width && cur.height == f.height &&
                              cur.frameInterval == f.frameInterval && cur.topDown == f.topDown;
            FreeMediaType(current);
            if (!same) hr = graph->Reconnect(this);
        }
    }
    return hr;
}

STDMETHODIMP VCamPin::GetFormat(AM_MEDIA_TYPE** ppmt) {
    if (!ppmt) return E_POINTER;
    EnterCriticalSection(&filter_->lock_);
    VideoFormat f = connected_ ? format_ : (hasPreferred_ ? preferred_ : VideoFormat{kSizes[0][0], kSizes[0][1], kDefaultInterval, false});
    LeaveCriticalSection(&filter_->lock_);
    *ppmt = CreateMediaType(f);
    return *ppmt ? S_OK : E_OUTOFMEMORY;
}

STDMETHODIMP VCamPin::GetNumberOfCapabilities(int* count, int* size) {
    if (!count || !size) return E_POINTER;
    *count = kNumSizes;
    *size = sizeof(VIDEO_STREAM_CONFIG_CAPS);
    return S_OK;
}

STDMETHODIMP VCamPin::GetStreamCaps(int index, AM_MEDIA_TYPE** ppmt, BYTE* capsBytes) {
    if (!ppmt || !capsBytes) return E_POINTER;
    if (index < 0 || index >= kNumSizes) return S_FALSE;
    const VideoFormat f{kSizes[index][0], kSizes[index][1], kDefaultInterval, false};
    *ppmt = CreateMediaType(f);
    if (!*ppmt) return E_OUTOFMEMORY;
    auto* caps = reinterpret_cast<VIDEO_STREAM_CONFIG_CAPS*>(capsBytes);
    ZeroMemory(caps, sizeof(*caps));
    caps->guid = FORMAT_VideoInfo;
    caps->VideoStandard = 0;
    caps->InputSize.cx = caps->MinCroppingSize.cx = caps->MaxCroppingSize.cx = f.width;
    caps->InputSize.cy = caps->MinCroppingSize.cy = caps->MaxCroppingSize.cy = f.height;
    caps->MinOutputSize = caps->InputSize;
    caps->MaxOutputSize = caps->InputSize;
    caps->CropGranularityX = caps->CropGranularityY = 1;
    caps->CropAlignX = caps->CropAlignY = 1;
    caps->OutputGranularityX = caps->OutputGranularityY = 1;
    caps->MinFrameInterval = kMinInterval;
    caps->MaxFrameInterval = kMaxInterval;
    const LONG bitsPerFrame = StrideOf(f.width) * f.height * 8;
    caps->MinBitsPerSecond = bitsPerFrame * 5;
    caps->MaxBitsPerSecond = bitsPerFrame * 60;
    return S_OK;
}

STDMETHODIMP VCamPin::Get(REFGUID set, DWORD id, LPVOID, DWORD, LPVOID data, DWORD cb, DWORD* returned) {
    if (set != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
    if (id != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
    if (!data && !returned) return E_POINTER;
    if (returned) *returned = sizeof(GUID);
    if (!data) return S_OK;
    if (cb < sizeof(GUID)) return E_UNEXPECTED;
    *static_cast<GUID*>(data) = PIN_CATEGORY_CAPTURE;
    return S_OK;
}

STDMETHODIMP VCamPin::QuerySupported(REFGUID set, DWORD id, DWORD* support) {
    if (set != AMPROPSETID_Pin) return E_PROP_SET_UNSUPPORTED;
    if (id != AMPROPERTY_PIN_CATEGORY) return E_PROP_ID_UNSUPPORTED;
    if (support) *support = KSPROPERTY_SUPPORT_GET;
    return S_OK;
}

// Streaming --------------------------------------------------------------------------------

HRESULT VCamPin::Active() {
    if (!allocator_ || !input_) return VFW_E_NOT_CONNECTED;
    HRESULT hr = allocator_->Commit();
    if (FAILED(hr)) return hr;
    if (!stopEvent_) stopEvent_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    ResetEvent(stopEvent_);
    if (!thread_) thread_ = CreateThread(nullptr, 0, ThreadProc, this, 0, nullptr);
    return thread_ ? S_OK : E_FAIL;
}

void VCamPin::Inactive() {
    StopStreaming();
    if (allocator_) allocator_->Decommit();
}

void VCamPin::StopStreaming() {
    if (thread_) {
        SetEvent(stopEvent_);
        // Decommit wakes a GetBuffer() that is waiting for a free sample.
        if (allocator_) allocator_->Decommit();
        WaitForSingleObject(thread_, INFINITE);
        CloseHandle(thread_);
        thread_ = nullptr;
    }
    if (stopEvent_) {
        CloseHandle(stopEvent_);
        stopEvent_ = nullptr;
    }
}

DWORD WINAPI VCamPin::ThreadProc(LPVOID self) {
    // GDI and COM calls happen on this thread.
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    static_cast<VCamPin*>(self)->StreamLoop();
    CoUninitialize();
    return 0;
}

void VCamPin::StreamLoop() {
    const VideoFormat f = format_;
    const REFERENCE_TIME interval = f.frameInterval;
    const long imageSize = static_cast<long>(StrideOf(f.width)) * f.height;
    REFERENCE_TIME next = 0;
    bool first = true;
    DWORD waitMs = 0;
    while (WaitForSingleObject(stopEvent_, waitMs) == WAIT_TIMEOUT) {
        if (filter_->State() != State_Running) {
            waitMs = 10;
            first = true;
            next = 0;
            continue;
        }
        const REFERENCE_TIME now = filter_->StreamTime();
        if (!first && now < next) {
            waitMs = static_cast<DWORD>((next - now) / 10000);
            if (waitMs == 0) waitMs = 1;
            continue;
        }
        IMediaSample* sample = nullptr;
        if (FAILED(allocator_->GetBuffer(&sample, nullptr, nullptr, 0)) || !sample) {
            waitMs = 5;
            continue;
        }
        BYTE* data = nullptr;
        if (SUCCEEDED(sample->GetPointer(&data)) && sample->GetSize() >= imageSize) {
            FillSample(data, f);
            sample->SetActualDataLength(imageSize);
            REFERENCE_TIME t0 = now > 0 ? now : 0;
            REFERENCE_TIME t1 = t0 + interval;
            sample->SetTime(&t0, &t1);
            sample->SetSyncPoint(TRUE);
            sample->SetDiscontinuity(first ? TRUE : FALSE);
            const HRESULT hr = input_->Receive(sample);
            sample->Release();
            if (hr == S_FALSE || FAILED(hr)) {
                // Downstream refused (flushing or stopping); back off until the state changes.
                waitMs = 20;
                continue;
            }
        } else {
            sample->Release();
        }
        first = false;
        next += interval;
        if (next < now) next = now + interval;  // never burst to catch up
        waitMs = 0;
    }
}

void VCamPin::FillSample(BYTE* data, const VideoFormat& f) {
    int sw = 0, sh = 0;
    if (reader_.Read(rgb_, sw, sh)) {
        BlitFrame(rgb_.data(), sw, sh, data, f.width, f.height, f.topDown);
        return;
    }
    if (placeholderW_ != f.width || placeholderH_ != f.height) {
        placeholder_ = RenderPlaceholder(f.width, f.height);
        placeholderW_ = f.width;
        placeholderH_ = f.height;
    }
    const int stride = StrideOf(f.width);
    if (!f.topDown) {
        memcpy(data, placeholder_.data(), placeholder_.size());
    } else {
        for (int y = 0; y < f.height; ++y)
            memcpy(data + static_cast<size_t>(y) * stride, placeholder_.data() + static_cast<size_t>(f.height - 1 - y) * stride, stride);
    }
}

// ---------------------------------------------------------------------------------------------
// COM plumbing and registration
// ---------------------------------------------------------------------------------------------

class ClassFactory final : public IClassFactory {
public:
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return 2; }  // static instance
    STDMETHODIMP_(ULONG) Release() override { return 1; }
    STDMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** ppv) override {
        if (!ppv) return E_POINTER;
        *ppv = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* filter = new (std::nothrow) VCamFilter();
        if (!filter) return E_OUTOFMEMORY;
        const HRESULT hr = filter->QueryInterface(riid, ppv);
        filter->Release();
        return hr;
    }
    STDMETHODIMP LockServer(BOOL lock) override {
        if (lock) ++g_locks;
        else --g_locks;
        return S_OK;
    }
};

static ClassFactory g_factory;

extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** ppv) {
    if (clsid != CLSID_CamAndroidVCam) return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, ppv);
}

STDAPI DllCanUnloadNow() { return (g_objects == 0 && g_locks == 0) ? S_OK : S_FALSE; }

static LONG SetString(HKEY root, const wchar_t* path, const wchar_t* name, const wchar_t* value) {
    HKEY key;
    LONG rc = RegCreateKeyExW(root, path, 0, nullptr, 0, KEY_WRITE, nullptr, &key, nullptr);
    if (rc != ERROR_SUCCESS) return rc;
    rc = RegSetValueExW(key, name, 0, REG_SZ, reinterpret_cast<const BYTE*>(value),
                        static_cast<DWORD>((wcslen(value) + 1) * sizeof(wchar_t)));
    RegCloseKey(key);
    return rc;
}

/** Registers under HKLM (all users, needs admin) or HKCU (current user, no admin). */
static HRESULT Register(HKEY root, bool install) {
    wchar_t clsidKey[128], instanceKey[192];
    swprintf(clsidKey, 128, L"Software\\Classes\\CLSID\\%ls", kClsidString);
    swprintf(instanceKey, 192, L"Software\\Classes\\CLSID\\%ls\\Instance\\%ls", kCategoryString, kClsidString);
    if (!install) {
        RegDeleteTreeW(root, clsidKey);
        RegDeleteTreeW(root, instanceKey);
        return S_OK;
    }
    wchar_t path[MAX_PATH];
    if (!GetModuleFileNameW(g_module, path, MAX_PATH)) return HRESULT_FROM_WIN32(GetLastError());
    wchar_t inproc[160];
    swprintf(inproc, 160, L"%ls\\InprocServer32", clsidKey);
    LONG rc = SetString(root, clsidKey, nullptr, kFriendlyName);
    if (rc == ERROR_SUCCESS) rc = SetString(root, inproc, nullptr, path);
    if (rc == ERROR_SUCCESS) rc = SetString(root, inproc, L"ThreadingModel", L"Both");
    if (rc == ERROR_SUCCESS) rc = SetString(root, instanceKey, L"FriendlyName", kFriendlyName);
    if (rc == ERROR_SUCCESS) rc = SetString(root, instanceKey, L"CLSID", kClsidString);
    if (rc != ERROR_SUCCESS) {
        Register(root, false);
        return HRESULT_FROM_WIN32(rc);
    }
    return S_OK;
}

STDAPI DllRegisterServer() { return Register(HKEY_LOCAL_MACHINE, true); }
STDAPI DllUnregisterServer() { return Register(HKEY_LOCAL_MACHINE, false); }

// regsvr32 /n /i:user camandroid-vcam64.dll      -> per-user install (no admin)
// regsvr32 /u /n /i:user camandroid-vcam64.dll   -> per-user uninstall
STDAPI DllInstall(BOOL install, LPCWSTR cmdLine) {
    const bool user = cmdLine && _wcsicmp(cmdLine, L"user") == 0;
    return Register(user ? HKEY_CURRENT_USER : HKEY_LOCAL_MACHINE, install != FALSE);
}
