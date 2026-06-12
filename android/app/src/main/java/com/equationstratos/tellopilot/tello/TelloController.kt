package com.equationstratos.tellopilot.tello

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketAddress
import java.net.SocketException

data class TelloState(
    val battery: Int = -1,
    val height: Int = 0,
    val flightTime: Int = 0,
)

/**
 * Pilote le Tello via le SDK officiel (texte sur UDP) :
 *  - commandes vers 192.168.10.1:8889, réponses sur le même socket
 *  - télémétrie reçue en local sur le port 8890
 *
 * Même protocole que tello.py à la racine du dépôt.
 */
class TelloController(
    private val scope: CoroutineScope,
    private val onState: (TelloState) -> Unit,
    private val onLog: (String) -> Unit,
) {
    companion object {
        const val TELLO_IP = "192.168.10.1"
        const val COMMAND_PORT = 8889
        const val STATE_PORT = 8890
        private const val RC_INTERVAL_MS = 50L
        private const val RESPONSE_TIMEOUT_MS = 7_000L
    }

    // Consignes RC dans [-100, 100], lues en continu par rcLoop().
    @Volatile var leftRight = 0
    @Volatile var forwardBackward = 0
    @Volatile var upDown = 0
    @Volatile var yaw = 0

    @Volatile private var connected = false
    val isConnected: Boolean get() = connected

    private var commandSocket: DatagramSocket? = null
    private var stateSocket: DatagramSocket? = null
    private var telloAddress: InetAddress? = null
    private val responses = Channel<String>(Channel.UNLIMITED)
    private val commandMutex = Mutex()
    private val jobs = mutableListOf<Job>()
    @Volatile private var lastStateAtMs = 0L

    /** Ouvre les sockets et passe le drone en mode SDK (commande « command »). */
    suspend fun connect(): Boolean = withContext(Dispatchers.IO) {
        close()
        try {
            telloAddress = InetAddress.getByName(TELLO_IP)
            commandSocket = openSocket(COMMAND_PORT)
            stateSocket = openSocket(STATE_PORT)
        } catch (e: Exception) {
            onLog("Erreur socket : ${e.message}")
            return@withContext false
        }
        jobs += scope.launch(Dispatchers.IO) { receiveResponses() }
        jobs += scope.launch(Dispatchers.IO) { receiveState() }

        var ok = false
        repeat(3) {
            if (!ok) {
                val r = sendCommand("command", 3_000L)
                ok = r != null && r.lowercase().startsWith("ok")
            }
        }
        if (ok) {
            connected = true
            jobs += scope.launch(Dispatchers.IO) { rcLoop() }
            jobs += scope.launch(Dispatchers.IO) { batteryFallbackLoop() }
        } else {
            close()
        }
        ok
    }

    fun close() {
        connected = false
        jobs.forEach { it.cancel() }
        jobs.clear()
        runCatching { commandSocket?.close() }
        runCatching { stateSocket?.close() }
        commandSocket = null
        stateSocket = null
    }

    suspend fun takeoff(): Boolean = expectOk("takeoff", 20_000L)
    suspend fun land(): Boolean = expectOk("land", 20_000L)
    suspend fun flip(direction: String): Boolean = expectOk("flip $direction", 10_000L)
    suspend fun streamOn(): Boolean = expectOk("streamon")
    suspend fun streamOff(): Boolean = expectOk("streamoff")

    /** Coupe les moteurs immédiatement (le drone tombe). */
    suspend fun emergency() {
        repeat(3) {
            sendRaw("emergency")
            delay(50)
        }
        onLog("URGENCE : moteurs coupés")
    }

    private suspend fun expectOk(command: String, timeoutMs: Long = RESPONSE_TIMEOUT_MS): Boolean =
        sendCommand(command, timeoutMs)?.lowercase()?.startsWith("ok") == true

    /** Envoie une commande et attend sa réponse. Une seule commande à la fois. */
    suspend fun sendCommand(command: String, timeoutMs: Long = RESPONSE_TIMEOUT_MS): String? =
        commandMutex.withLock {
            withContext(Dispatchers.IO) {
                val socket = commandSocket ?: return@withContext null
                val address = telloAddress ?: return@withContext null
                // Purge les réponses tardives d'une commande précédente.
                while (responses.tryReceive().isSuccess) {
                    // vide le canal
                }
                val data = command.toByteArray(Charsets.UTF_8)
                try {
                    socket.send(DatagramPacket(data, data.size, address, COMMAND_PORT))
                } catch (e: Exception) {
                    onLog("Envoi « $command » impossible : ${e.message}")
                    return@withContext null
                }
                val response = withTimeoutOrNull(timeoutMs) { responses.receive() }
                if (response == null) {
                    onLog("« $command » : pas de réponse")
                } else {
                    onLog("« $command » → $response")
                }
                response
            }
        }

    /** Envoi sans réponse attendue (rc, emergency). */
    private fun sendRaw(command: String) {
        val socket = commandSocket ?: return
        val address = telloAddress ?: return
        val data = command.toByteArray(Charsets.UTF_8)
        runCatching { socket.send(DatagramPacket(data, data.size, address, COMMAND_PORT)) }
    }

    /**
     * Envoie la consigne RC toutes les 50 ms, même au neutre : cela sert aussi
     * de signal de vie (le Tello atterrit après 15 s sans commande).
     */
    private suspend fun rcLoop() {
        while (scope.isActive && connected) {
            sendRaw("rc $leftRight $forwardBackward $upDown $yaw")
            delay(RC_INTERVAL_MS)
        }
    }

    /** Les anciens firmwares n'émettent pas de télémétrie : on interroge la batterie. */
    private suspend fun batteryFallbackLoop() {
        while (scope.isActive && connected) {
            delay(8_000L)
            if (connected && System.currentTimeMillis() - lastStateAtMs > 8_000L) {
                val battery = sendCommand("battery?", 3_000L)?.trim()?.toIntOrNull()
                if (battery != null) onState(TelloState(battery = battery))
            }
        }
    }

    private fun receiveResponses() {
        val socket = commandSocket ?: return
        val buffer = ByteArray(2048)
        while (!socket.isClosed) {
            try {
                val packet = DatagramPacket(buffer, buffer.size)
                socket.receive(packet)
                val text = String(packet.data, 0, packet.length, Charsets.UTF_8).trim()
                if (text.isNotEmpty()) responses.trySend(text)
            } catch (e: SocketException) {
                break
            } catch (e: Exception) {
                onLog("Réception : ${e.message}")
            }
        }
    }

    private fun receiveState() {
        val socket = stateSocket ?: return
        val buffer = ByteArray(2048)
        while (!socket.isClosed) {
            try {
                val packet = DatagramPacket(buffer, buffer.size)
                socket.receive(packet)
                lastStateAtMs = System.currentTimeMillis()
                parseState(String(packet.data, 0, packet.length, Charsets.UTF_8))
            } catch (e: SocketException) {
                break
            } catch (_: Exception) {
                // paquet d'état illisible : on ignore
            }
        }
    }

    /** Format : « pitch:0;roll:0;yaw:0;...;bat:87;...;h:30;time:12;... » */
    private fun parseState(text: String) {
        val fields = HashMap<String, String>()
        for (part in text.trim().split(';')) {
            val sep = part.indexOf(':')
            if (sep > 0) fields[part.substring(0, sep)] = part.substring(sep + 1)
        }
        val battery = fields["bat"]?.trim()?.toIntOrNull() ?: return
        onState(
            TelloState(
                battery = battery,
                height = fields["h"]?.trim()?.toIntOrNull() ?: 0,
                flightTime = fields["time"]?.trim()?.toIntOrNull() ?: 0,
            )
        )
    }

    private fun openSocket(port: Int): DatagramSocket =
        DatagramSocket(null as SocketAddress?).apply {
            reuseAddress = true
            bind(InetSocketAddress(port))
        }
}
