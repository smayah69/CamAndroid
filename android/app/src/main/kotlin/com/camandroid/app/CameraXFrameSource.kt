package com.camandroid.app

import android.content.Context
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CaptureRequest
import android.util.Log
import android.util.Range
import android.util.Size
import android.view.OrientationEventListener
import android.view.Surface
import androidx.annotation.OptIn
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.Camera
import androidx.camera.core.CameraInfo
import androidx.camera.core.CameraSelector
import androidx.camera.core.ExtendableBuilder
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.Preview
import androidx.camera.core.SurfaceRequest
import androidx.camera.core.UseCase
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.camandroid.core.FrameDecimator
import com.camandroid.core.FrameRate
import com.camandroid.core.FrameSink
import com.camandroid.core.FrameSource
import com.camandroid.core.Protocol
import com.camandroid.core.StreamConfig
import com.camandroid.core.Yuv
import com.camandroid.core.YuvPlane
import java.io.ByteArrayOutputStream
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/**
 * Captures frames with CameraX. H.264 goes camera -> hardware encoder through a surface, with no
 * CPU copies; JPEG (for older desktop apps) goes through ImageAnalysis and a software encoder.
 */
class CameraXFrameSource(
    private val context: Context,
    private val owner: LifecycleOwner,
    private val onError: (String) -> Unit,
) : FrameSource {
    private val mainExecutor = ContextCompat.getMainExecutor(context)
    private val analysisExecutor: ExecutorService = Executors.newSingleThreadExecutor()
    private val providerFuture = ProcessCameraProvider.getInstance(context)
    private val h264Available by lazy { H264Encoder.isAvailable() }

    @Volatile private var config: StreamConfig? = null
    @Volatile private var sink: FrameSink? = null
    @Volatile private var frontFacing = false
    @Volatile private var deviceOrientation = 0
    @Volatile private var sensorRotation = 0
    @Volatile private var encoder: H264Encoder? = null
    private var camera: Camera? = null
    private var bound: StreamConfig? = null

    // Analyzer-thread state.
    @Volatile private var decimator: FrameDecimator? = null
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

    override fun codecs(): List<String> =
        if (h264Available) listOf(StreamConfig.CODEC_H264, StreamConfig.CODEC_JPEG) else listOf(StreamConfig.CODEC_JPEG)

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
                current.height != config.height || current.fps != config.fps || current.codec != config.codec
            ) {
                bind(config)
            } else {
                setTorch(config.torch)
                if (current.quality != config.quality) encoder?.setBitrate(config.bitrate)
                bound = config
            }
        }
    }

    override fun requestKeyFrame() {
        encoder?.requestKeyFrame()
    }

    override fun stop() {
        config = null
        sink = null
        onMain { unbind() }
    }

    fun release() {
        config = null
        sink = null
        onMain {
            unbind()
            analysisExecutor.shutdown()
        }
    }

    private fun unbind() {
        orientationListener.disable()
        providerFuture.get().unbindAll()
        camera = null
        bound = null
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
        val facing = if (selector == CameraSelector.DEFAULT_FRONT_CAMERA) CameraSelector.LENS_FACING_FRONT else CameraSelector.LENS_FACING_BACK
        val info = provider.availableCameraInfos.firstOrNull { it.lensFacing == facing }
        val fpsRange = info?.let { pickFpsRange(it, c.fps) }
        val resolution = ResolutionSelector.Builder()
            .setAspectRatioStrategy(
                if (c.width * 3 == c.height * 4) AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY
                else AspectRatioStrategy.RATIO_16_9_FALLBACK_AUTO_STRATEGY,
            )
            .setResolutionStrategy(
                ResolutionStrategy(Size(c.width, c.height), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER),
            )
            .build()
        val useCase: UseCase = if (c.codec == StreamConfig.CODEC_H264) {
            val builder = Preview.Builder()
                .setResolutionSelector(resolution)
                // Rotation is sent with every frame, computed from how the phone is held, so the
                // stream works the same with the screen off or the phone on a tripod.
                .setTargetRotation(Surface.ROTATION_0)
            fpsRange?.let { setFpsRange(builder, it) }
            builder.build().also { it.setSurfaceProvider(mainExecutor) { request -> provideEncoderSurface(request, c) } }
        } else {
            decimator = null
            val builder = ImageAnalysis.Builder()
                .setResolutionSelector(resolution)
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                .setTargetRotation(Surface.ROTATION_0)
            fpsRange?.let { setFpsRange(builder, it) }
            builder.build().also { it.setAnalyzer(analysisExecutor, ::analyze) }
        }
        provider.unbindAll()
        frontFacing = selector == CameraSelector.DEFAULT_FRONT_CAMERA
        camera = provider.bindToLifecycle(owner, selector, useCase)
        bound = c
        setTorch(c.torch)
        Log.i(TAG, "Streaming ${c.codec} ${c.width}x${c.height}@${c.fps}, camera fps range $fpsRange")
    }

    /** Creates an encoder sized to what the camera will produce and hands its surface to CameraX. */
    private fun provideEncoderSurface(request: SurfaceRequest, c: StreamConfig) {
        val size = request.resolution
        val enc = try {
            H264Encoder(size.width, size.height, c.fps, c.bitrate, ::onEncodedFrame) { onError(it) }
        } catch (e: Exception) {
            Log.e(TAG, "Cannot start H.264 encoder", e)
            onError("Cannot start the video encoder: ${e.message}")
            request.willNotProvideSurface()
            return
        }
        Log.i(TAG, "Encoder ${enc.name} ${size.width}x${size.height} at ${c.bitrate / 1000} kbit/s")
        request.setTransformationInfoListener(mainExecutor) { sensorRotation = it.rotationDegrees }
        encoder = enc
        request.provideSurface(enc.surface, mainExecutor) {
            if (encoder === enc) encoder = null
            enc.release()
        }
    }

    private fun onEncodedFrame(data: ByteArray, keyFrame: Boolean, timestampUs: Long) {
        val s = sink ?: return
        val flags = frameFlags() or (if (keyFrame) Protocol.FLAG_KEY_FRAME else 0)
        s.onFrame(data, frameRotation(sensorRotation), flags, timestampUs)
    }

    @OptIn(ExperimentalCamera2Interop::class)
    private fun pickFpsRange(info: CameraInfo, fps: Int): Range<Int>? {
        val ranges = Camera2CameraInfo.from(info)
            .getCameraCharacteristic(CameraCharacteristics.CONTROL_AE_AVAILABLE_TARGET_FPS_RANGES)
            ?.map { it.lower to it.upper } ?: return null
        return FrameRate.pickRange(ranges, fps)?.let { Range(it.first, it.second) }
    }

    @OptIn(ExperimentalCamera2Interop::class)
    private fun <T> setFpsRange(builder: ExtendableBuilder<T>, range: Range<Int>) {
        Camera2Interop.Extender(builder).setCaptureRequestOption(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE, range)
    }

    private fun setTorch(on: Boolean) {
        val cam = camera ?: return
        if (cam.cameraInfo.hasFlashUnit()) cam.cameraControl.enableTorch(on)
    }

    private fun frameFlags() = if (frontFacing) Protocol.FLAG_FRONT_CAMERA else 0

    /**
     * Same formula as Camera2's JPEG_ORIENTATION: sensor rotation adjusted by how the device is
     * held (mirrored for the front camera).
     */
    private fun frameRotation(sensor: Int): Int {
        val held = if (frontFacing) (360 - deviceOrientation) % 360 else deviceOrientation
        return (sensor + held) % 360
    }

    private fun analyze(image: ImageProxy) {
        try {
            val c = config ?: return
            val s = sink ?: return
            val d = decimator ?: FrameDecimator(c.fps).also { decimator = it }
            if (!d.accept(System.nanoTime())) return

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
            val flags = frameFlags() or Protocol.FLAG_KEY_FRAME
            s.onFrame(jpeg.toByteArray(), frameRotation(image.imageInfo.rotationDegrees), flags, image.imageInfo.timestamp / 1000)
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
