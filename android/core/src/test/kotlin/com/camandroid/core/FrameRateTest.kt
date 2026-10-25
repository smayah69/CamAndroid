package com.camandroid.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class FrameRateTest {
    private val typical = listOf(15 to 30, 30 to 30, 15 to 60, 60 to 60, 7 to 30)

    @Test
    fun picksFixedRangeForRequestedRate() {
        assertEquals(60 to 60, FrameRate.pickRange(typical, 60))
        assertEquals(30 to 30, FrameRate.pickRange(typical, 30))
        assertEquals(30 to 30, FrameRate.pickRange(typical, 24))
    }

    @Test
    fun fallsBackToFastestRange() {
        assertEquals(30 to 30, FrameRate.pickRange(listOf(15 to 30, 30 to 30), 60))
        assertEquals(null, FrameRate.pickRange(emptyList(), 30))
    }

    private fun countAccepted(sourceFps: Int, targetFps: Int, seconds: Int = 10): Int {
        val d = FrameDecimator(targetFps)
        val step = 1_000_000_000L / sourceFps
        return (0 until sourceFps * seconds).count { i ->
            val jitter = if (i % 2 == 0) 1_500_000L else -1_500_000L
            d.accept(i * step + jitter)
        }
    }

    @Test
    fun decimatorKeepsTargetRate() {
        assertEquals(300, countAccepted(30, 30))
        assertEquals(600, countAccepted(60, 60))
        assertTrue(countAccepted(30, 24) in 235..245, "24 fps from a 30 fps camera")
        assertTrue(countAccepted(60, 30) in 295..305, "30 fps from a 60 fps camera")
        assertEquals(300, countAccepted(30, 60), "never more than the camera delivers")
    }
}
