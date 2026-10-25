package com.camandroid.core

import java.awt.Color
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO
import kotlin.concurrent.thread

/** Produces synthetic JPEG frames (a moving bar) so the server can be tested without a camera. */
class FakeFrameSource : FrameSource {
    @Volatile var config: StreamConfig? = null
        private set
    @Volatile private var worker: Thread? = null

    override fun cameras() = listOf(StreamConfig.CAMERA_BACK, StreamConfig.CAMERA_FRONT)

    @Synchronized
    override fun start(config: StreamConfig, sink: FrameSink) {
        this.config = config
        worker = thread(name = "fake-camera", isDaemon = true) {
            var n = 0
            try {
                while (!Thread.currentThread().isInterrupted) {
                    val c = this.config ?: break
                    val flags = if (c.camera == StreamConfig.CAMERA_FRONT) Protocol.FLAG_FRONT_CAMERA else 0
                    sink.onFrame(render(c, n++), 0, flags, System.nanoTime() / 1000)
                    Thread.sleep(1000L / c.fps)
                }
            } catch (_: InterruptedException) {
            }
        }
    }

    override fun update(config: StreamConfig) {
        this.config = config
    }

    @Synchronized
    override fun stop() {
        config = null
        worker?.interrupt()
        worker = null
    }

    private fun render(c: StreamConfig, n: Int): ByteArray {
        val img = BufferedImage(c.width, c.height, BufferedImage.TYPE_INT_RGB)
        val g = img.createGraphics()
        g.color = Color(30, 30, 60)
        g.fillRect(0, 0, c.width, c.height)
        g.color = Color.ORANGE
        g.fillRect((n * 8) % c.width, 0, c.width / 10, c.height)
        g.dispose()
        val out = ByteArrayOutputStream()
        ImageIO.write(img, "jpg", out)
        return out.toByteArray()
    }
}
