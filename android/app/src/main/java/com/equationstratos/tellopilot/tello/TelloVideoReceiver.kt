package com.equationstratos.tellopilot.tello

import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.SocketAddress
import java.net.SocketException
import kotlin.concurrent.thread

/**
 * Reçoit le flux H.264 du Tello sur UDP 11111.
 *
 * Le drone découpe chaque trame en paquets de 1460 octets ;
 * un paquet plus court marque la fin de la trame.
 */
class TelloVideoReceiver(private val onFrame: (ByteArray) -> Unit) {

    companion object {
        const val VIDEO_PORT = 11111
        private const val CHUNK_SIZE = 1460
    }

    @Volatile private var running = false
    private var socket: DatagramSocket? = null

    fun start() {
        if (running) return
        running = true
        thread(name = "tello-video", isDaemon = true) {
            val sock = try {
                DatagramSocket(null as SocketAddress?).apply {
                    reuseAddress = true
                    bind(InetSocketAddress(VIDEO_PORT))
                }
            } catch (e: Exception) {
                running = false
                return@thread
            }
            socket = sock
            val buffer = ByteArray(4096)
            val frame = ByteArrayOutputStream(64 * 1024)
            while (running) {
                try {
                    val packet = DatagramPacket(buffer, buffer.size)
                    sock.receive(packet)
                    frame.write(packet.data, 0, packet.length)
                    if (packet.length < CHUNK_SIZE) {
                        onFrame(frame.toByteArray())
                        frame.reset()
                    }
                } catch (e: SocketException) {
                    break
                } catch (_: Exception) {
                    frame.reset()
                }
            }
        }
    }

    fun stop() {
        running = false
        runCatching { socket?.close() }
        socket = null
    }
}
