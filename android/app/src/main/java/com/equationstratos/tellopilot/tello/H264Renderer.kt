package com.equationstratos.tellopilot.tello

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * Décode le flux H.264 du Tello (960×720) avec MediaCodec
 * et l'affiche directement sur la Surface fournie.
 */
class H264Renderer(
    private val surface: Surface,
    private val onError: (String) -> Unit,
) {
    companion object {
        private const val WIDTH = 960
        private const val HEIGHT = 720
        private const val QUEUE_CAPACITY = 60
    }

    private val queue = LinkedBlockingQueue<ByteArray>(QUEUE_CAPACITY)
    @Volatile private var running = false
    @Volatile private var seenSps = false
    private var worker: Thread? = null

    fun start() {
        if (running) return
        running = true
        seenSps = false
        worker = thread(name = "h264-decoder", isDaemon = true) { decodeLoop() }
    }

    fun stop() {
        running = false
        queue.clear()
        worker?.join(1_000)
        worker = null
    }

    /** Reçoit une trame réassemblée. Avant le premier SPS, tout est jeté. */
    fun feed(frame: ByteArray) {
        if (!running) return
        if (!seenSps) {
            if (!containsSps(frame)) return
            seenSps = true
        }
        if (!queue.offer(frame)) {
            // File pleine : on jette la plus vieille trame pour limiter la latence.
            queue.poll()
            queue.offer(frame)
        }
    }

    private fun decodeLoop() {
        while (running) {
            var codec: MediaCodec? = null
            try {
                val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_AVC, WIDTH, HEIGHT)
                format.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 256 * 1024)
                codec = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_AVC)
                codec.configure(format, surface, null, 0)
                codec.start()
                val info = MediaCodec.BufferInfo()
                while (running) {
                    val frame = queue.poll(100, TimeUnit.MILLISECONDS)
                    if (frame != null) {
                        val inIndex = codec.dequeueInputBuffer(20_000)
                        if (inIndex >= 0) {
                            val input = codec.getInputBuffer(inIndex)
                            if (input != null && frame.size <= input.capacity()) {
                                input.clear()
                                input.put(frame)
                                codec.queueInputBuffer(
                                    inIndex, 0, frame.size, System.nanoTime() / 1_000, 0
                                )
                            } else {
                                codec.queueInputBuffer(inIndex, 0, 0, 0, 0)
                            }
                        }
                    }
                    var outIndex = codec.dequeueOutputBuffer(info, 0)
                    while (outIndex >= 0) {
                        codec.releaseOutputBuffer(outIndex, true)
                        outIndex = codec.dequeueOutputBuffer(info, 0)
                    }
                }
            } catch (e: InterruptedException) {
                break
            } catch (e: Exception) {
                if (running) {
                    onError("Décodeur vidéo : ${e.message ?: e.javaClass.simpleName}")
                    seenSps = false
                    queue.clear()
                    try {
                        Thread.sleep(500)
                    } catch (_: InterruptedException) {
                        break
                    }
                }
            } finally {
                runCatching { codec?.stop() }
                runCatching { codec?.release() }
            }
        }
    }

    /** Cherche un start code 00 00 00 01 suivi d'un NAL de type 7 (SPS). */
    private fun containsSps(data: ByteArray): Boolean {
        for (i in 0..data.size - 5) {
            if (data[i] == 0.toByte() && data[i + 1] == 0.toByte() &&
                data[i + 2] == 0.toByte() && data[i + 3] == 1.toByte() &&
                (data[i + 4].toInt() and 0x1F) == 7
            ) {
                return true
            }
        }
        return false
    }
}
