package com.equationstratos.tellopilot.net

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest

/**
 * Force le trafic de l'appli à passer par le Wi-Fi.
 *
 * Indispensable depuis Android 10 : le réseau du Tello n'a pas d'accès
 * Internet, donc Android privilégie les données mobiles et les paquets
 * UDP n'atteignent jamais le drone si on ne lie pas le processus au Wi-Fi.
 */
class WifiBinder(context: Context) {

    private val connectivityManager =
        context.applicationContext.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    private var callback: ConnectivityManager.NetworkCallback? = null

    /** Appelle [onResult] une seule fois : true si le Wi-Fi a été lié. */
    fun bind(onResult: (Boolean) -> Unit) {
        unbind()
        var notified = false
        fun notifyOnce(success: Boolean) {
            if (!notified) {
                notified = true
                onResult(success)
            }
        }

        val request = NetworkRequest.Builder()
            .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
            .build()
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                connectivityManager.bindProcessToNetwork(network)
                notifyOnce(true)
            }

            override fun onUnavailable() {
                notifyOnce(false)
            }
        }
        callback = cb
        try {
            connectivityManager.requestNetwork(request, cb, 10_000)
        } catch (e: Exception) {
            notifyOnce(false)
        }
    }

    fun unbind() {
        callback?.let { runCatching { connectivityManager.unregisterNetworkCallback(it) } }
        callback = null
        runCatching { connectivityManager.bindProcessToNetwork(null) }
    }
}
