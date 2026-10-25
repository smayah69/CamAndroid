package com.camandroid.core

data class StreamConfig(
    val width: Int = 1280,
    val height: Int = 720,
    val fps: Int = 30,
    val quality: Int = 80,
    val camera: String = CAMERA_BACK,
    val torch: Boolean = false,
) {
    companion object {
        const val CAMERA_BACK = "back"
        const val CAMERA_FRONT = "front"
    }
}

fun interface FrameSink {
    /** [rotation] is the clockwise rotation in degrees the receiver must apply to display upright. */
    fun onFrame(jpeg: ByteArray, rotation: Int, flags: Int, timestampUs: Long)
}

/** A camera (or a fake) that produces JPEG frames. Implementations must be thread-safe. */
interface FrameSource {
    fun cameras(): List<String>
    fun start(config: StreamConfig, sink: FrameSink)
    fun update(config: StreamConfig)
    fun stop()
}
