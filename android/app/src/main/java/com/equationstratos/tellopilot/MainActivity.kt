package com.equationstratos.tellopilot

import android.os.Bundle
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.widget.Button
import android.widget.TextView
import android.widget.ToggleButton
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import com.equationstratos.tellopilot.net.WifiBinder
import com.equationstratos.tellopilot.tello.H264Renderer
import com.equationstratos.tellopilot.tello.TelloController
import com.equationstratos.tellopilot.tello.TelloState
import com.equationstratos.tellopilot.tello.TelloVideoReceiver
import com.equationstratos.tellopilot.ui.JoystickView
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var wifiBinder: WifiBinder
    private lateinit var controller: TelloController
    private var videoReceiver: TelloVideoReceiver? = null
    private var renderer: H264Renderer? = null

    private lateinit var videoSurface: SurfaceView
    private lateinit var connectButton: Button
    private lateinit var takeoffButton: Button
    private lateinit var landButton: Button
    private lateinit var emergencyButton: Button
    private lateinit var telemetryText: TextView
    private lateinit var statusText: TextView
    private lateinit var speedToggle: ToggleButton
    private lateinit var leftStick: JoystickView
    private lateinit var rightStick: JoystickView

    private var videoWanted = false
    private var surfaceReady = false

    private val speedFactor: Float
        get() = if (speedToggle.isChecked) 1.0f else 0.4f

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        setupFullscreen()

        wifiBinder = WifiBinder(this)
        controller = TelloController(
            scope = lifecycleScope,
            onState = { state -> runOnUiThread { showState(state) } },
            onLog = { message -> runOnUiThread { statusText.text = message } },
        )

        videoSurface = findViewById(R.id.videoSurface)
        connectButton = findViewById(R.id.connectButton)
        takeoffButton = findViewById(R.id.takeoffButton)
        landButton = findViewById(R.id.landButton)
        emergencyButton = findViewById(R.id.emergencyButton)
        telemetryText = findViewById(R.id.telemetryText)
        statusText = findViewById(R.id.statusText)
        speedToggle = findViewById(R.id.speedToggle)
        leftStick = findViewById(R.id.leftStick)
        rightStick = findViewById(R.id.rightStick)

        videoSurface.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                surfaceReady = true
                maybeStartRenderer()
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) = Unit

            override fun surfaceDestroyed(holder: SurfaceHolder) {
                surfaceReady = false
                renderer?.stop()
                renderer = null
            }
        })

        connectButton.setOnClickListener {
            if (controller.isConnected) disconnectAll() else startConnection()
        }
        takeoffButton.setOnClickListener {
            lifecycleScope.launch { controller.takeoff() }
        }
        landButton.setOnClickListener {
            lifecycleScope.launch { controller.land() }
        }
        emergencyButton.setOnClickListener {
            statusText.setText(R.string.status_emergency_hint)
        }
        emergencyButton.setOnLongClickListener {
            lifecycleScope.launch { controller.emergency() }
            true
        }

        findViewById<Button>(R.id.flipForwardButton).setOnClickListener { doFlip("f") }
        findViewById<Button>(R.id.flipBackButton).setOnClickListener { doFlip("b") }
        findViewById<Button>(R.id.flipLeftButton).setOnClickListener { doFlip("l") }
        findViewById<Button>(R.id.flipRightButton).setOnClickListener { doFlip("r") }

        // Mode 2 : stick gauche = gaz + lacet, stick droit = tangage + roulis.
        leftStick.onMove = { x, y ->
            controller.yaw = (x * 100 * speedFactor).toInt()
            controller.upDown = (y * 100 * speedFactor).toInt()
        }
        rightStick.onMove = { x, y ->
            controller.leftRight = (x * 100 * speedFactor).toInt()
            controller.forwardBackward = (y * 100 * speedFactor).toInt()
        }

        setConnectedUi(false)
    }

    override fun onDestroy() {
        stopVideo()
        controller.close()
        wifiBinder.unbind()
        super.onDestroy()
    }

    private fun setupFullscreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        val insetsController = WindowInsetsControllerCompat(window, window.decorView)
        insetsController.hide(WindowInsetsCompat.Type.systemBars())
        insetsController.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        val root = findViewById<android.view.View>(R.id.root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val cutout = insets.getInsets(WindowInsetsCompat.Type.displayCutout())
            view.setPadding(cutout.left, cutout.top, cutout.right, cutout.bottom)
            insets
        }
    }

    private fun startConnection() {
        connectButton.isEnabled = false
        statusText.setText(R.string.status_binding)
        wifiBinder.bind { bound ->
            runOnUiThread {
                if (!bound) statusText.setText(R.string.status_bind_failed)
                lifecycleScope.launch {
                    statusText.setText(R.string.status_connecting)
                    val connected = controller.connect()
                    connectButton.isEnabled = true
                    if (connected) {
                        setConnectedUi(true)
                        statusText.setText(R.string.status_connected)
                        if (controller.streamOn()) {
                            startVideo()
                        } else {
                            statusText.setText(R.string.status_video_failed)
                        }
                    } else {
                        statusText.setText(R.string.status_connect_failed)
                        wifiBinder.unbind()
                    }
                }
            }
        }
    }

    private fun disconnectAll() {
        lifecycleScope.launch {
            if (controller.isConnected) {
                runCatching { controller.streamOff() }
            }
            stopVideo()
            controller.close()
            wifiBinder.unbind()
            setConnectedUi(false)
            statusText.setText(R.string.status_idle)
            telemetryText.setText(R.string.telemetry_placeholder)
        }
    }

    private fun setConnectedUi(connected: Boolean) {
        connectButton.setText(if (connected) R.string.disconnect else R.string.connect)
        takeoffButton.isEnabled = connected
        landButton.isEnabled = connected
        emergencyButton.isEnabled = connected
        findViewById<Button>(R.id.flipForwardButton).isEnabled = connected
        findViewById<Button>(R.id.flipBackButton).isEnabled = connected
        findViewById<Button>(R.id.flipLeftButton).isEnabled = connected
        findViewById<Button>(R.id.flipRightButton).isEnabled = connected
    }

    private fun showState(state: TelloState) {
        if (state.battery >= 0) {
            telemetryText.text =
                getString(R.string.telemetry_format, state.battery, state.height, state.flightTime)
        }
    }

    private fun doFlip(direction: String) {
        lifecycleScope.launch { controller.flip(direction) }
    }

    private fun startVideo() {
        videoWanted = true
        if (videoReceiver == null) {
            videoReceiver = TelloVideoReceiver { frame -> renderer?.feed(frame) }.also { it.start() }
        }
        maybeStartRenderer()
    }

    private fun maybeStartRenderer() {
        if (videoWanted && surfaceReady && renderer == null) {
            renderer = H264Renderer(videoSurface.holder.surface) { message ->
                runOnUiThread { statusText.text = message }
            }.also { it.start() }
        }
    }

    private fun stopVideo() {
        videoWanted = false
        videoReceiver?.stop()
        videoReceiver = null
        renderer?.stop()
        renderer = null
    }
}
