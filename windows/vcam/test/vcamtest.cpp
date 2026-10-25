// Test consumer for the CamAndroid virtual camera. It does what a video-call app does: find the
// device in "Video Capture Sources", build a capture graph, and pull frames.
//
//   vcamtest [--size WxH] [--fps N] [--seconds N] [--expect R,G,B] [--feed R,G,B] [--list]
//
// --feed also acts as the writer (solid colour frames through shared memory), to test the
// reader path without camandroid.exe. Exit code 0 means frames arrived (and matched --expect).

#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#include <windows.h>
#include <dshow.h>

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <string>

#include "../src/shared_frame.h"

static const CLSID CLSID_SampleGrabber_ = {0xC1F400A0, 0x3F08, 0x11d3, {0x9F, 0x0B, 0x00, 0x60, 0x08, 0x03, 0x9E, 0x37}};
static const CLSID CLSID_NullRenderer_ = {0xC1F400A4, 0x3F08, 0x11d3, {0x9F, 0x0B, 0x00, 0x60, 0x08, 0x03, 0x9E, 0x37}};
static const IID IID_ISampleGrabber_ = {0x6B652FFF, 0x11FE, 0x4fce, {0x92, 0xAD, 0x02, 0x66, 0xB5, 0xD7, 0xC7, 0x8F}};
static const IID IID_ISampleGrabberCB_ = {0x0579154A, 0x2B53, 0x4994, {0xB0, 0xD0, 0xE7, 0x73, 0x14, 0x8E, 0xFF, 0x85}};

// qedit.h is not in current Windows SDKs; declare the two interfaces we need.
struct ISampleGrabberCB_ : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE SampleCB(double time, IMediaSample* sample) = 0;
    virtual HRESULT STDMETHODCALLTYPE BufferCB(double time, BYTE* buffer, long len) = 0;
};
struct ISampleGrabber_ : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE SetOneShot(BOOL oneShot) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetMediaType(const AM_MEDIA_TYPE* type) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetConnectedMediaType(AM_MEDIA_TYPE* type) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetBufferSamples(BOOL buffer) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetCurrentBuffer(long* size, long* buffer) = 0;
    virtual HRESULT STDMETHODCALLTYPE GetCurrentSample(IMediaSample** sample) = 0;
    virtual HRESULT STDMETHODCALLTYPE SetCallback(ISampleGrabberCB_* cb, long which) = 0;
};

struct Counter : public ISampleGrabberCB_ {
    std::atomic<long> frames{0};
    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown || riid == IID_ISampleGrabberCB_) {
            *ppv = this;
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }
    STDMETHODIMP_(ULONG) AddRef() override { return 2; }
    STDMETHODIMP_(ULONG) Release() override { return 1; }
    STDMETHODIMP SampleCB(double, IMediaSample*) override {
        ++frames;
        return S_OK;
    }
    STDMETHODIMP BufferCB(double, BYTE*, long) override { return S_OK; }
};

static int Fail(const char* what, HRESULT hr) {
    std::printf("FAIL: %s (hr=0x%08lx)\n", what, static_cast<unsigned long>(hr));
    return 1;
}

static volatile bool g_feeding = true;
static bool g_split = false;  // writer: top half colour, bottom half black

static DWORD WINAPI Feed(LPVOID arg) {
    const int* rgb = static_cast<const int*>(arg);
    const uint32_t w = 640, h = 360;
    const size_t size = sizeof(VCamHeader) + VCAM_MAX_FRAME_BYTES;
    HANDLE map = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, static_cast<DWORD>(size),
                                    VCAM_MAPPING_NAME);
    if (!map) return 1;
    auto* base = static_cast<uint8_t*>(MapViewOfFile(map, FILE_MAP_WRITE, 0, 0, 0));
    auto* hdr = reinterpret_cast<VCamHeader*>(base);
    uint8_t* px = base + sizeof(VCamHeader);
    for (uint64_t n = 0; g_feeding; ++n) {
        hdr->seq++;  // odd: writing
        MemoryBarrier();
        hdr->magic = VCAM_MAGIC;
        hdr->version = VCAM_VERSION;
        hdr->width = w;
        hdr->height = h;
        hdr->fps = 30;
        for (size_t i = 0; i < size_t(w) * h; ++i) {
            const bool black = g_split && i >= size_t(w) * h / 2;
            px[i * 3] = black ? 0 : static_cast<uint8_t>(rgb[0]);
            px[i * 3 + 1] = black ? 0 : static_cast<uint8_t>(rgb[1]);
            px[i * 3 + 2] = black ? 0 : static_cast<uint8_t>(rgb[2]);
        }
        FILETIME ft;
        GetSystemTimeAsFileTime(&ft);
        ULARGE_INTEGER t;
        t.LowPart = ft.dwLowDateTime;
        t.HighPart = ft.dwHighDateTime;
        hdr->timestamp_ms = (t.QuadPart - 116444736000000000ULL) / 10000ULL;
        hdr->frame_index = n;
        MemoryBarrier();
        hdr->seq++;  // even: stable
        Sleep(33);
    }
    UnmapViewOfFile(base);
    CloseHandle(map);
    return 0;
}

int main(int argc, char** argv) {
    int width = 0, height = 0, seconds = 3, fps = 0;
    int expect[3] = {-1, -1, -1};
    int feed[3] = {-1, -1, -1};
    bool listOnly = false;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--size" && i + 1 < argc) std::sscanf(argv[++i], "%dx%d", &width, &height);
        else if (a == "--seconds" && i + 1 < argc) seconds = std::atoi(argv[++i]);
        else if (a == "--fps" && i + 1 < argc) fps = std::atoi(argv[++i]);
        else if (a == "--expect" && i + 1 < argc) std::sscanf(argv[++i], "%d,%d,%d", &expect[0], &expect[1], &expect[2]);
        else if (a == "--feed" && i + 1 < argc) std::sscanf(argv[++i], "%d,%d,%d", &feed[0], &feed[1], &feed[2]);
        else if (a == "--list") listOnly = true;
        else if (a == "--split") g_split = true;
    }

    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    HANDLE feeder = feed[0] >= 0 ? CreateThread(nullptr, 0, Feed, feed, 0, nullptr) : nullptr;

    ICreateDevEnum* devEnum = nullptr;
    HRESULT hr = CoCreateInstance(CLSID_SystemDeviceEnum, nullptr, CLSCTX_INPROC_SERVER, IID_ICreateDevEnum,
                                  reinterpret_cast<void**>(&devEnum));
    if (FAILED(hr)) return Fail("SystemDeviceEnum", hr);
    IEnumMoniker* monikers = nullptr;
    hr = devEnum->CreateClassEnumerator(CLSID_VideoInputDeviceCategory, &monikers, 0);
    if (hr != S_OK) return Fail("no video capture devices registered", hr);
    IBaseFilter* source = nullptr;
    IMoniker* m = nullptr;
    while (monikers->Next(1, &m, nullptr) == S_OK) {
        IPropertyBag* bag = nullptr;
        if (SUCCEEDED(m->BindToStorage(nullptr, nullptr, IID_IPropertyBag, reinterpret_cast<void**>(&bag)))) {
            VARIANT v;
            VariantInit(&v);
            if (SUCCEEDED(bag->Read(L"FriendlyName", &v, nullptr))) {
                std::printf("device: %ls\n", v.bstrVal);
                if (!source && wcscmp(v.bstrVal, L"CamAndroid") == 0)
                    m->BindToObject(nullptr, nullptr, IID_IBaseFilter, reinterpret_cast<void**>(&source));
                VariantClear(&v);
            }
            bag->Release();
        }
        m->Release();
    }
    if (!source) return Fail("CamAndroid device not found", E_FAIL);
    if (listOnly) {
        std::printf("OK: CamAndroid is registered\n");
        return 0;
    }

    IGraphBuilder* graph = nullptr;
    ICaptureGraphBuilder2* builder = nullptr;
    if (FAILED(hr = CoCreateInstance(CLSID_FilterGraph, nullptr, CLSCTX_INPROC_SERVER, IID_IGraphBuilder,
                                     reinterpret_cast<void**>(&graph))))
        return Fail("FilterGraph", hr);
    if (FAILED(hr = CoCreateInstance(CLSID_CaptureGraphBuilder2, nullptr, CLSCTX_INPROC_SERVER,
                                     IID_ICaptureGraphBuilder2, reinterpret_cast<void**>(&builder))))
        return Fail("CaptureGraphBuilder2", hr);
    builder->SetFiltergraph(graph);
    graph->AddFilter(source, L"CamAndroid");

    if (width > 0) {
        IAMStreamConfig* cfg = nullptr;
        hr = builder->FindInterface(&PIN_CATEGORY_CAPTURE, &MEDIATYPE_Video, source, IID_IAMStreamConfig,
                                    reinterpret_cast<void**>(&cfg));
        if (FAILED(hr)) return Fail("IAMStreamConfig", hr);
        int count = 0, size = 0;
        cfg->GetNumberOfCapabilities(&count, &size);
        bool set = false;
        for (int i = 0; i < count && !set; ++i) {
            AM_MEDIA_TYPE* mt = nullptr;
            VIDEO_STREAM_CONFIG_CAPS caps;
            if (cfg->GetStreamCaps(i, &mt, reinterpret_cast<BYTE*>(&caps)) != S_OK) continue;
            auto* vih = reinterpret_cast<VIDEOINFOHEADER*>(mt->pbFormat);
            const bool rateMatches = fps <= 0 || vih->AvgTimePerFrame == 10000000 / fps;
            if (vih->bmiHeader.biWidth == width && vih->bmiHeader.biHeight == height && rateMatches)
                set = SUCCEEDED(hr = cfg->SetFormat(mt));
            CoTaskMemFree(mt->pbFormat);
            CoTaskMemFree(mt);
        }
        cfg->Release();
        if (!set) return Fail("SetFormat for requested size", hr);
    }

    IBaseFilter* grabberFilter = nullptr;
    ISampleGrabber_* grabber = nullptr;
    IBaseFilter* sink = nullptr;
    if (FAILED(hr = CoCreateInstance(CLSID_SampleGrabber_, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter,
                                     reinterpret_cast<void**>(&grabberFilter))))
        return Fail("SampleGrabber (qedit.dll)", hr);
    grabberFilter->QueryInterface(IID_ISampleGrabber_, reinterpret_cast<void**>(&grabber));
    AM_MEDIA_TYPE want = {};
    want.majortype = MEDIATYPE_Video;
    want.subtype = MEDIASUBTYPE_RGB24;
    grabber->SetMediaType(&want);
    grabber->SetBufferSamples(TRUE);
    Counter counter;
    grabber->SetCallback(&counter, 0);
    graph->AddFilter(grabberFilter, L"Grabber");
    if (FAILED(hr = CoCreateInstance(CLSID_NullRenderer_, nullptr, CLSCTX_INPROC_SERVER, IID_IBaseFilter,
                                     reinterpret_cast<void**>(&sink))))
        return Fail("NullRenderer", hr);
    graph->AddFilter(sink, L"Sink");
    if (FAILED(hr = builder->RenderStream(&PIN_CATEGORY_CAPTURE, &MEDIATYPE_Video, source, grabberFilter, sink)))
        return Fail("RenderStream", hr);

    IMediaControl* control = nullptr;
    graph->QueryInterface(IID_IMediaControl, reinterpret_cast<void**>(&control));
    if (FAILED(hr = control->Run())) return Fail("Run", hr);
    Sleep(seconds * 1000);

    AM_MEDIA_TYPE connected = {};
    grabber->GetConnectedMediaType(&connected);
    auto* vih = reinterpret_cast<VIDEOINFOHEADER*>(connected.pbFormat);
    const int w = vih ? vih->bmiHeader.biWidth : 0;
    const int h = vih ? std::abs(vih->bmiHeader.biHeight) : 0;
    long bytes = 0;
    grabber->GetCurrentBuffer(&bytes, nullptr);
    int center[3] = {-1, -1, -1};
    int top[3] = {-1, -1, -1}, bottom[3] = {-1, -1, -1};
    if (bytes > 0 && w > 0) {
        auto* buf = static_cast<BYTE*>(std::malloc(bytes));
        grabber->GetCurrentBuffer(&bytes, reinterpret_cast<long*>(buf));
        const int stride = (w * 3 + 3) & ~3;
        const BYTE* p = buf + static_cast<size_t>(h / 2) * stride + (w / 2) * 3;
        center[0] = p[2];
        center[1] = p[1];
        center[2] = p[0];
        // Positive biHeight means a bottom-up DIB: the first row in memory is the bottom one.
        const bool bottomUp = vih->bmiHeader.biHeight > 0;
        const int topRow = bottomUp ? h - 1 - h / 4 : h / 4;
        const int bottomRow = bottomUp ? h / 4 : h - 1 - h / 4;
        const BYTE* t = buf + static_cast<size_t>(topRow) * stride + (w / 2) * 3;
        const BYTE* b = buf + static_cast<size_t>(bottomRow) * stride + (w / 2) * 3;
        for (int c = 0; c < 3; ++c) {
            top[c] = t[2 - c];
            bottom[c] = b[2 - c];
        }
        std::free(buf);
    }
    control->Stop();
    g_feeding = false;
    if (feeder) WaitForSingleObject(feeder, 2000);
    const long frames = counter.frames;
    std::printf("frames=%ld size=%dx%d center=%d,%d,%d top=%d,%d,%d bottom=%d,%d,%d\n", frames, w, h, center[0],
                center[1], center[2], top[0], top[1], top[2], bottom[0], bottom[1], bottom[2]);

    if (frames < seconds * 5) return Fail("too few frames", E_FAIL);
    if (fps > 0 && frames < static_cast<long>(seconds) * fps * 8 / 10) return Fail("frame rate too low", E_FAIL);
    if (width > 0 && (w != width || h != height)) return Fail("negotiated size differs", E_FAIL);
    if (expect[0] >= 0) {
        const int* probe = g_split ? top : center;
        for (int c = 0; c < 3; ++c)
            if (std::abs(probe[c] - expect[c]) > 12) return Fail("unexpected pixel colour", E_FAIL);
        if (g_split && bottom[0] + bottom[1] + bottom[2] > 36) return Fail("image is upside down", E_FAIL);
    }
    std::printf("OK\n");
    return 0;
}
