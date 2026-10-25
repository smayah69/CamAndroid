// Shared-memory frame channel between camandroid.exe (writer) and the CamAndroid virtual camera
// DLL (reader, loaded inside Zoom/Teams/Chrome/...). Mirrored in desktop/camandroid_desktop/vcam.py.
//
// Layout: one named file mapping "Local\CamAndroidVCam" (per logon session) of
// sizeof(VCamHeader) + VCAM_MAX_FRAME_BYTES bytes. Pixels are RGB24, top-down, tightly packed.
//
// Concurrency is a seqlock: the writer makes `seq` odd, writes the frame, then makes it even.
// A reader copies the frame and accepts it only if `seq` was even and unchanged across the copy.
#pragma once
#include <stdint.h>

#define VCAM_MAPPING_NAME L"Local\\CamAndroidVCam"
#define VCAM_MAGIC 0x43564143u  // "CAVC"
#define VCAM_VERSION 1u
#define VCAM_MAX_WIDTH 3840u
#define VCAM_MAX_HEIGHT 2160u
#define VCAM_MAX_FRAME_BYTES (VCAM_MAX_WIDTH * VCAM_MAX_HEIGHT * 3u)
#define VCAM_STALE_MS 3000u  // no new frame for this long -> show the "waiting" picture

#pragma pack(push, 1)
struct VCamHeader {
    uint32_t magic;
    uint32_t version;
    uint32_t width;
    uint32_t height;
    volatile uint32_t seq;
    uint32_t fps;           // frame rate the writer is producing (hint)
    uint64_t frame_index;
    uint64_t timestamp_ms;  // Unix epoch milliseconds of the last write
    uint8_t reserved[24];
};
#pragma pack(pop)

static_assert(sizeof(VCamHeader) == 64, "header layout is part of the protocol");
