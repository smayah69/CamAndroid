// H.264 decoder for camandroid.exe built on Windows' own Media Foundation decoder, so the app
// does not have to ship FFmpeg. Plain C API, used from Python through ctypes
// (desktop/camandroid_desktop/mfdecoder.py):
//
//   cah_create()                   -> decoder, or NULL when Media Foundation has no H.264 decoder
//   cah_decode(d, data, len)       -> 1 new picture, 0 needs more input, < 0 error (HRESULT)
//   cah_size(d, &width, &height)   -> size of the latest picture
//   cah_to_rgb(d, rgb, w, h, pitch)-> latest picture as RGB24, scaled (bilinear) to w x h
//   cah_destroy(d)
//
// Input is Annex-B H.264 access units (SPS/PPS before key frames), one per call.
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0A00
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mfapi.h>
#include <mferror.h>
#include <mfidl.h>
#include <mftransform.h>

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <new>
#include <thread>
#include <vector>

// Defined here instead of linking wmcodecdspuuid.lib.
static const CLSID kH264DecoderClsid = {0x62CE7E72, 0x4C71, 0x4d20, {0xB1, 0x5D, 0x45, 0x28, 0x31, 0xA8, 0x7D, 0x9D}};
static const GUID kLowLatency = {0x9c27891a, 0xed7a, 0x40e1, {0x88, 0xe8, 0xb2, 0x27, 0x27, 0xa0, 0x24, 0xee}};

template <typename T>
static void SafeRelease(T*& p) {
    if (p) {
        p->Release();
        p = nullptr;
    }
}

namespace {

struct Decoder {
    IMFTransform* mft = nullptr;
    bool outputSet = false;
    UINT32 width = 0, height = 0;  // visible picture
    UINT32 allocHeight = 0;        // rows in the decoder's buffer (e.g. 1088 for 1080p)
    LONG stride = 0;               // bytes per row when the buffer has no 2D interface
    LONGLONG time = 0;
    std::vector<uint8_t> nv12;     // latest picture, tightly packed: Y rows then interleaved UV rows
    UINT32 picW = 0, picH = 0;
};

HRESULT SetOutputType(Decoder* d) {
    for (DWORD i = 0;; ++i) {
        IMFMediaType* type = nullptr;
        HRESULT hr = d->mft->GetOutputAvailableType(0, i, &type);
        if (FAILED(hr)) return hr;
        GUID subtype = GUID_NULL;
        type->GetGUID(MF_MT_SUBTYPE, &subtype);
        if (subtype != MFVideoFormat_NV12) {
            type->Release();
            continue;
        }
        hr = d->mft->SetOutputType(0, type, 0);
        if (SUCCEEDED(hr)) {
            UINT32 w = 0, h = 0;
            MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &w, &h);
            d->allocHeight = h;
            UINT32 stride = 0;
            d->stride = SUCCEEDED(type->GetUINT32(MF_MT_DEFAULT_STRIDE, &stride)) && static_cast<INT32>(stride) > 0
                            ? static_cast<LONG>(stride)
                            : static_cast<LONG>(w);
            MFVideoArea area = {};
            if (SUCCEEDED(type->GetBlob(MF_MT_MINIMUM_DISPLAY_APERTURE, reinterpret_cast<UINT8*>(&area), sizeof(area),
                                        nullptr)) &&
                area.Area.cx > 0 && area.Area.cy > 0) {
                w = static_cast<UINT32>(area.Area.cx);
                h = static_cast<UINT32>(area.Area.cy);
            }
            d->width = w & ~1u;
            d->height = h & ~1u;
            d->outputSet = true;
        }
        type->Release();
        return hr;
    }
}

void CopyPlanes(Decoder* d, const BYTE* src, LONG pitch) {
    const UINT32 w = d->width, h = d->height;
    d->nv12.resize(static_cast<size_t>(w) * h * 3 / 2);
    uint8_t* dst = d->nv12.data();
    for (UINT32 r = 0; r < h; ++r) memcpy(dst + static_cast<size_t>(r) * w, src + static_cast<size_t>(r) * pitch, w);
    const BYTE* uv = src + static_cast<size_t>(pitch) * d->allocHeight;
    uint8_t* duv = dst + static_cast<size_t>(w) * h;
    for (UINT32 r = 0; r < h / 2; ++r)
        memcpy(duv + static_cast<size_t>(r) * w, uv + static_cast<size_t>(r) * pitch, w);
    d->picW = w;
    d->picH = h;
}

HRESULT StorePicture(Decoder* d, IMFSample* sample) {
    IMFMediaBuffer* buffer = nullptr;
    HRESULT hr = sample->ConvertToContiguousBuffer(&buffer);
    if (FAILED(hr)) return hr;
    IMF2DBuffer* buffer2d = nullptr;
    if (SUCCEEDED(buffer->QueryInterface(IID_PPV_ARGS(&buffer2d)))) {
        BYTE* data = nullptr;
        LONG pitch = 0;
        hr = buffer2d->Lock2D(&data, &pitch);
        if (SUCCEEDED(hr)) {
            CopyPlanes(d, data, pitch);
            buffer2d->Unlock2D();
        }
        buffer2d->Release();
    } else {
        BYTE* data = nullptr;
        DWORD maxLen = 0, len = 0;
        hr = buffer->Lock(&data, &maxLen, &len);
        if (SUCCEEDED(hr)) {
            if (len >= static_cast<DWORD>(d->stride) * d->allocHeight * 3 / 2) CopyPlanes(d, data, d->stride);
            else hr = E_UNEXPECTED;
            buffer->Unlock();
        }
    }
    buffer->Release();
    return hr;
}

/** Pulls every picture the decoder has ready. Returns 1 if at least one arrived. */
int Drain(Decoder* d) {
    int got = 0;
    for (;;) {
        if (!d->outputSet) {
            const HRESULT hr = SetOutputType(d);
            if (FAILED(hr)) return hr == MF_E_TRANSFORM_TYPE_NOT_SET ? got : static_cast<int>(hr);
        }
        MFT_OUTPUT_STREAM_INFO info = {};
        HRESULT hr = d->mft->GetOutputStreamInfo(0, &info);
        if (FAILED(hr)) return static_cast<int>(hr);
        MFT_OUTPUT_DATA_BUFFER out = {};
        IMFSample* ours = nullptr;
        if (!(info.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES | MFT_OUTPUT_STREAM_CAN_PROVIDE_SAMPLES))) {
            IMFMediaBuffer* buffer = nullptr;
            if (FAILED(hr = MFCreateSample(&ours))) return static_cast<int>(hr);
            if (FAILED(hr = MFCreateMemoryBuffer(info.cbSize, &buffer))) {
                ours->Release();
                return static_cast<int>(hr);
            }
            ours->AddBuffer(buffer);
            buffer->Release();
            out.pSample = ours;
        }
        DWORD status = 0;
        hr = d->mft->ProcessOutput(0, 1, &out, &status);
        SafeRelease(out.pEvents);
        if (hr == MF_E_TRANSFORM_STREAM_CHANGE) {
            // New SPS (size change): pick the output type again.
            if (out.pSample != ours) SafeRelease(out.pSample);
            SafeRelease(ours);
            d->outputSet = false;
            continue;
        }
        if (SUCCEEDED(hr) && out.pSample) {
            hr = StorePicture(d, out.pSample);
            if (SUCCEEDED(hr)) got = 1;
        }
        if (out.pSample != ours) SafeRelease(out.pSample);
        SafeRelease(ours);
        if (hr == MF_E_TRANSFORM_NEED_MORE_INPUT) return got;
        if (FAILED(hr)) return static_cast<int>(hr);
    }
}

inline uint8_t Clamp(int v) { return static_cast<uint8_t>(v < 0 ? 0 : v > 255 ? 255 : v); }

/** NV12 (BT.601, limited range) to RGB24 with bilinear luma scaling, rows [y0, y1). */
void ConvertRows(const Decoder* d, uint8_t* rgb, int ow, int oh, int pitch, int y0, int y1, const int* xs,
                 const uint8_t* xf) {
    const int w = static_cast<int>(d->picW), h = static_cast<int>(d->picH);
    const uint8_t* Y = d->nv12.data();
    const uint8_t* UV = Y + static_cast<size_t>(w) * h;
    for (int oy = y0; oy < y1; ++oy) {
        const int sy = std::max(0, static_cast<int>(((oy * 2 + 1) * static_cast<int64_t>(h) * 128) / oh) - 128);
        const int r0 = std::min(sy >> 8, h - 1), r1 = std::min(r0 + 1, h - 1), fy = sy & 0xFF;
        const uint8_t* y0row = Y + static_cast<size_t>(r0) * w;
        const uint8_t* y1row = Y + static_cast<size_t>(r1) * w;
        const uint8_t* uvrow = UV + static_cast<size_t>(std::min((r0 + (fy >> 7)) / 2, h / 2 - 1)) * w;
        uint8_t* out = rgb + static_cast<size_t>(oy) * pitch;
        for (int ox = 0; ox < ow; ++ox) {
            const int c0 = xs[ox], c1 = std::min(c0 + 1, w - 1), fx = xf[ox];
            const int top = y0row[c0] * (256 - fx) + y0row[c1] * fx;
            const int bottom = y1row[c0] * (256 - fx) + y1row[c1] * fx;
            const int yv = (top * (256 - fy) + bottom * fy + 32768) >> 16;
            const int cx = std::min((c0 + (fx >> 7)) & ~1, w - 2);
            const int u = uvrow[cx] - 128, v = uvrow[cx + 1] - 128;
            const int c = 298 * (yv - 16) + 128;
            out[0] = Clamp((c + 409 * v) >> 8);
            out[1] = Clamp((c - 100 * u - 208 * v) >> 8);
            out[2] = Clamp((c + 516 * u) >> 8);
            out += 3;
        }
    }
}

}  // namespace

extern "C" {

__declspec(dllexport) void* cah_create() {
    auto* d = new (std::nothrow) Decoder();
    if (!d) return nullptr;
    // COM stays initialised on this thread: the decoder may be destroyed from another one, and
    // CoUninitialize must run on the thread that initialised it.
    CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    IMFMediaType* input = nullptr;
    IMFAttributes* attrs = nullptr;
    HRESULT hr = MFStartup(MF_VERSION, MFSTARTUP_LITE);
    if (SUCCEEDED(hr))
        hr = CoCreateInstance(kH264DecoderClsid, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&d->mft));
    if (SUCCEEDED(hr) && SUCCEEDED(d->mft->GetAttributes(&attrs))) {
        attrs->SetUINT32(kLowLatency, TRUE);  // output each picture as soon as it is decoded
        attrs->Release();
    }
    if (SUCCEEDED(hr)) hr = MFCreateMediaType(&input);
    if (SUCCEEDED(hr)) hr = input->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    if (SUCCEEDED(hr)) hr = input->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_H264);
    if (SUCCEEDED(hr)) hr = input->SetUINT32(MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive);
    if (SUCCEEDED(hr)) hr = d->mft->SetInputType(0, input, 0);
    SafeRelease(input);
    if (SUCCEEDED(hr)) {
        SetOutputType(d);  // may only become possible after the first SPS
        d->mft->ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
        d->mft->ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
        return d;
    }
    SafeRelease(d->mft);
    MFShutdown();
    delete d;
    return nullptr;
}

__declspec(dllexport) int cah_decode(void* handle, const uint8_t* data, int len) {
    auto* d = static_cast<Decoder*>(handle);
    if (!d || !data || len <= 0) return static_cast<int>(E_INVALIDARG);
    IMFSample* sample = nullptr;
    IMFMediaBuffer* buffer = nullptr;
    HRESULT hr = MFCreateMemoryBuffer(static_cast<DWORD>(len), &buffer);
    if (SUCCEEDED(hr)) {
        BYTE* dst = nullptr;
        hr = buffer->Lock(&dst, nullptr, nullptr);
        if (SUCCEEDED(hr)) {
            memcpy(dst, data, static_cast<size_t>(len));
            buffer->Unlock();
            buffer->SetCurrentLength(static_cast<DWORD>(len));
        }
    }
    if (SUCCEEDED(hr)) hr = MFCreateSample(&sample);
    if (SUCCEEDED(hr)) hr = sample->AddBuffer(buffer);
    SafeRelease(buffer);
    if (FAILED(hr)) {
        SafeRelease(sample);
        return static_cast<int>(hr);
    }
    sample->SetSampleTime(d->time);
    sample->SetSampleDuration(166666);
    d->time += 166666;
    int got = 0;
    hr = d->mft->ProcessInput(0, sample, 0);
    if (hr == MF_E_NOTACCEPTING) {  // pictures are waiting: take them, then retry
        got = Drain(d);
        if (got < 0) {
            sample->Release();
            return got;
        }
        hr = d->mft->ProcessInput(0, sample, 0);
    }
    sample->Release();
    if (FAILED(hr)) return static_cast<int>(hr);
    const int more = Drain(d);
    return more < 0 ? more : (got | more);
}

__declspec(dllexport) int cah_size(void* handle, int* width, int* height) {
    auto* d = static_cast<Decoder*>(handle);
    if (!d || !width || !height || d->nv12.empty()) return 0;
    *width = static_cast<int>(d->picW);
    *height = static_cast<int>(d->picH);
    return 1;
}

__declspec(dllexport) int cah_to_rgb(void* handle, uint8_t* rgb, int width, int height, int pitch) {
    auto* d = static_cast<Decoder*>(handle);
    if (!d || !rgb || d->nv12.empty() || width <= 0 || height <= 0 || pitch < width * 3) return 0;
    const int w = static_cast<int>(d->picW);
    std::vector<int> xs(static_cast<size_t>(width));
    std::vector<uint8_t> xf(static_cast<size_t>(width));
    for (int ox = 0; ox < width; ++ox) {
        const int sx = std::max(0, static_cast<int>(((ox * 2 + 1) * static_cast<int64_t>(w) * 128) / width) - 128);
        xs[static_cast<size_t>(ox)] = std::min(sx >> 8, w - 1);
        xf[static_cast<size_t>(ox)] = static_cast<uint8_t>(xs[static_cast<size_t>(ox)] == w - 1 ? 0 : sx & 0xFF);
    }
    // Large pictures are split across cores; 1080p takes a few milliseconds.
    const unsigned cores = std::max(1u, std::min(8u, std::thread::hardware_concurrency()));
    const int parts = static_cast<int64_t>(width) * height >= 400000 ? static_cast<int>(cores) : 1;
    std::vector<std::thread> workers;
    for (int i = 1; i < parts; ++i) {
        const int y0 = height * i / parts, y1 = height * (i + 1) / parts;
        workers.emplace_back(ConvertRows, d, rgb, width, height, pitch, y0, y1, xs.data(), xf.data());
    }
    ConvertRows(d, rgb, width, height, pitch, 0, height / parts, xs.data(), xf.data());
    for (auto& t : workers) t.join();
    return 1;
}

__declspec(dllexport) void cah_destroy(void* handle) {
    auto* d = static_cast<Decoder*>(handle);
    if (!d) return;
    SafeRelease(d->mft);
    MFShutdown();
    delete d;
}

}  // extern "C"
