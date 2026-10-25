package com.camandroid.core

import java.nio.ByteBuffer
import kotlin.test.Test
import kotlin.test.assertContentEquals

class YuvTest {
    private val w = 4
    private val h = 2

    @Test
    fun planarTightlyPacked() {
        val y = YuvPlane(ByteBuffer.wrap(ByteArray(8) { it.toByte() }), rowStride = 4, pixelStride = 1)
        val u = YuvPlane(ByteBuffer.wrap(byteArrayOf(20, 21)), rowStride = 2, pixelStride = 1)
        val v = YuvPlane(ByteBuffer.wrap(byteArrayOf(30, 31)), rowStride = 2, pixelStride = 1)
        assertContentEquals(byteArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 30, 20, 31, 21), Yuv.toNv21(w, h, y, u, v))
    }

    @Test
    fun semiPlanarWithRowPadding() {
        // Typical camera layout: padded rows, U and V views into one interleaved buffer.
        val yData = byteArrayOf(0, 1, 2, 3, -1, -1, 4, 5, 6, 7) // rowStride 6, last row unpadded
        val uv = byteArrayOf(20, 30, 21, 31) // U V U V
        val y = YuvPlane(ByteBuffer.wrap(yData), rowStride = 6, pixelStride = 1)
        val u = YuvPlane(ByteBuffer.wrap(uv, 0, 3).slice(), rowStride = 4, pixelStride = 2)
        val v = YuvPlane(ByteBuffer.wrap(uv, 1, 3).slice(), rowStride = 4, pixelStride = 2)
        val y2 = YuvPlane(ByteBuffer.wrap(yData), rowStride = 6, pixelStride = 1)
        assertContentEquals(byteArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 30, 20, 31, 21), Yuv.toNv21(w, h, y, u, v))
        // Reusing the output buffer gives the same result.
        val buf = ByteArray(12)
        assertContentEquals(Yuv.toNv21(w, h, y2, u, v), Yuv.toNv21(w, h, y2, u, v, buf))
    }
}
