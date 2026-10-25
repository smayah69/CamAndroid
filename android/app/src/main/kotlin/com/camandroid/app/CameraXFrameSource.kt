package com.camandroid.app

import android.content.Context
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.util.Log
import android.util.Size
import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.camandroid.core.FrameSink
import com.camandroid.core.FrameSource
import com.camandroid.core.Protocol
import com.camandroid.core.StreamConfig
import com.camandroid.core.Yuv
import com.camandroid.core.YuvPlane
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** Captures frames with CameraX ImageAnalysis and encodes them as JPEG. */
class CameraXFrameSource(
    private val context: Context,
    private val owner: LifecycleOwner,
    private val onError: (String) -> Unit,
) : FrameSource {
    private val mainExecutor = ContextCompat.getMainExecutor(context)
    private val analysisExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val providerFuture = ProcessCameraProvider.getInstance(context)

    @Volatile private var config: StreamConfig? = null
    @Volatile private var sink: FrameSink? = null
    @Volatile private var frontFacing = false
    @Volatile private var deviceOrientation = 0
    private var camera: Camera? = null
    private var bound: StreamConfig? = null

    // Analyzer-thread state.
    private var lastFrameNs = 0L
    private var nv21: ByteArray? = null
    private val jpeg = ByteArrayOutputStream(256 * 1024)

    private val orientationListener = object : OrientationEventListener(context) {
        override fun onOrientationChanged(orientation: Int) {
            if (orientation != ORIENTATION_UNKNOWN) deviceOrientation = ((orientation + 45) / 90 * 90) % 360
        }
    }

    override fun cameras(): List<String> {
        val provider = providerFuture.get()
        return buildList {
            if (provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA)) add(StreamConfig.CAMERA_BACK)
            if (provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA)) add(StreamConfig.CAMERA_FRONT)
        }
    }

    override fun start(config: StreamConfig, sink: FrameSink) {
        this.sink = sink
        this.config = config
        onMain {
            orientationListener.enable()
            bind(config)
        }
    }

    override fun update(config: StreamConfig) {
        this.config = config
        onMain {
            val current = bound
            if (current == null || current.camera != config.camera || current.width != config.width ||
                current.height != config.height
            ) {
                bind(config)
            } else {
                setTorch(config.torch)
                bound = config
            }
        }
    }

    override fun stop() {
        config = null
        sink = null
        onMain {
            orientationListener.disable()
            providerFuture.get().unbindAll()
            camera = null
            bound = null
        }
    }

    fun release() {
        config = null
        sink = null
        onMain {
            orientationListener.disable()
            providerFuture.get().unbindAll()
            camera = null
            bound = null
            analysisExecutor.shutdown()
        }
    }

    private fun onMain(block: () -> Unit) {
        providerFuture.addListener({
            try {
                block()
            } catch (e: Exception) {
                Log.e(TAG, "Camera operation failed", e)
                onError("Camera error: ${e.message}")
            }
        }, mainExecutor)
    }

    private fun bind(c: StreamConfig) {
        if (config == null) return // stopped before we got here
        val provider = providerFuture.get()
        val wantFront = c.camera == StreamConfig.CAMERA_FRONT
        val selector = when {
            wantFront && provider.hasCamera(CameraSelector.DEFAULT_FRONT_CAMERA) -> CameraSelector.DEFAULT_FRONT_CAMERA
            provider.hasCamera(CameraSelector.DEFAULT_BACK_CAMERA) -> CameraSelector.DEFAULT_BACK_CAMERA
            else -> CameraSelector.DEFAULT_FRONT_CAMERA
        }
        val resolution = ResolutionSelector.Builder()
            .setResolutionStrategy(
                ResolutionStrategy(Size(c.width, c.height), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER),
            )
            .build()
        val analysis = ImageAnalysis.Builder()
            .setResolutionSelector(resolution)
            .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
            .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
            // Rotation is computed per frame from the device orientation, so the server
            // works the same with the screen off or the phone on a tripod.
            .setTargetRotation(Surface.ROTATION_0)
            .build()
        analysis.setAnalyzer(analysisExecutor, ::analyze)
        provider.unbindAll()
        frontFacing = selector == CameraSelector.DEFAULT_FRONT_CAMERA
        camera = provider.bindToLifecycle(owner, selector, analysis)
        bound = c
        setTorch(c.torch)
    }

    private fun setTorch(on: Boolean) {
        val cam = camera ?: return
        if (cam.cameraInfo.hasFlashUnit()) cam.cameraControl.enableTorch(on)
    }

    private fun analyze(image: ImageProxy) {
        try {
            val c = config ?: return
            val s = sink ?: return
            val now = System.nanoTime()
            if (now - lastFrameNs < 1_000_000_000L / c.fps * 9 / 10) return
            lastFrameNs = now

            val planes = image.planes
            val data = Yuv.toNv21(
                image.width, image.height,
                YuvPlane(planes[0].buffer, planes[0].rowStride, planes[0].pixelStride),
                YuvPlane(planes[1].buffer, planes[1].rowStride, planes[1].pixelStride),
                YuvPlane(planes[2].buffer, planes[2].rowStride, planes[2].pixelStride),
                nv21,
            )
            nv21 = data
            jpeg.reset()
            YuvImage(data, ImageFormat.NV21, image.width, image.height, null)
                .compressToJpeg(Rect(0, 0, image.width, image.height), c.quality, jpeg)

            // Same formula as Camera2's JPEG_ORIENTATION: sensor rotation adjusted by how the
            // device is held (mirrored for the front camera).
            val front = frontFacing
            val held = if (front) (360 - deviceOrientation) % 360 else deviceOrientation
            val rotation = (image.imageInfo.rotationDegrees + held) % 360
            val flags = if (front) Protocol.FLAG_FRONT_CAMERA else 0
            s.onFrame(jpeg.toByteArray(), rotation, flags, image.imageInfo.timestamp / 1000)
        } catch (e: Exception) {
            Log.w(TAG, "Dropping frame", e)
        } finally {
            image.close()
        }
    }

    private companion object {
        const val TAG = "CamAndroid"
    }
}
