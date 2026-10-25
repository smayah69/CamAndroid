package com.camandroid.core

object FrameRate {
    /**
     * Chooses the camera auto-exposure frame-rate range for [fps] from the ranges the camera
     * supports. The camera must not slow down in dim light, so the smallest upper bound that still
     * reaches [fps] wins, then the highest lower bound (ideally a fixed range such as [60, 60]).
     */
    fun pickRange(ranges: List<Pair<Int, Int>>, fps: Int): Pair<Int, Int>? {
        if (ranges.isEmpty()) return null
        val fast = ranges.filter { it.second >= fps }
        val pool = if (fast.isNotEmpty()) {
            val upper = fast.minOf { it.second }
            fast.filter { it.second == upper }
        } else {
            val upper = ranges.maxOf { it.second }
            ranges.filter { it.second == upper }
        }
        return pool.maxBy { it.first }
    }
}

/** Passes frames through at [fps] on average, whatever rate the camera delivers them at. */
class FrameDecimator(fps: Int) {
    private val intervalNs = 1_000_000_000L / fps.coerceAtLeast(1)
    private var nextNs = 0L
    private var started = false

    fun accept(nowNs: Long): Boolean {
        if (started && nowNs < nextNs - intervalNs / 2) return false
        // Keep a steady schedule; after a stall restart it instead of bursting to catch up.
        nextNs = if (!started || nowNs - nextNs > intervalNs) nowNs + intervalNs else nextNs + intervalNs
        started = true
        return true
    }
}
