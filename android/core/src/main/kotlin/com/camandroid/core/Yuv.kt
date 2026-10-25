package com.camandroid.core

import java.nio.ByteBuffer

/** Plane layout of a YUV_420_888 image, as exposed by Android's Image/ImageProxy. */
class YuvPlane(val buffer: ByteBuffer, val rowStride: Int, val pixelStride: Int)

object Yuv {
    /**
     * Packs a YUV_420_888 image into NV21 (Y plane followed by interleaved V/U), the format
     * android.graphics.YuvImage compresses to JPEG. Handles arbitrary row and pixel strides.
     */
    fun toNv21(width: Int, height: Int, y: YuvPlane, u: YuvPlane, v: YuvPlane, reuse: ByteArray? = null): ByteArray {
        val ySize = width * height
        val out = reuse?.takeIf { it.size == ySize + ySize / 2 } ?: ByteArray(ySize + ySize / 2)

        val yBuf = y.buffer.duplicate()
        if (y.pixelStride == 1 && y.rowStride == width) {
            yBuf.position(0)
            yBuf.get(out, 0, ySize)
        } else {
            val row = ByteArray(y.rowStride)
            var pos = 0
            for (r in 0 until height) {
                yBuf.position(r * y.rowStride)
                val len = minOf(y.rowStride, yBuf.remaining())
                yBuf.get(row, 0, len)
                for (c in 0 until width) out[pos++] = row[c * y.pixelStride]
            }
        }

        val chromaW = width / 2
        val chromaH = height / 2
        val uBuf = u.buffer.duplicate()
        val vBuf = v.buffer.duplicate()
        val uRow = ByteArray(u.rowStride)
        val vRow = ByteArray(v.rowStride)
        var pos = ySize
        for (r in 0 until chromaH) {
            uBuf.position(r * u.rowStride)
            uBuf.get(uRow, 0, minOf(u.rowStride, uBuf.remaining()))
            vBuf.position(r * v.rowStride)
            vBuf.get(vRow, 0, minOf(v.rowStride, vBuf.remaining()))
            for (c in 0 until chromaW) {
                out[pos++] = vRow[c * v.pixelStride]
                out[pos++] = uRow[c * u.pixelStride]
            }
        }
        return out
    }
}
