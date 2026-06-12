package com.equationstratos.tellopilot.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import kotlin.math.hypot
import kotlin.math.min

/**
 * Joystick virtuel. Renvoie x/y normalisés dans [-1, 1] :
 * x positif vers la droite, y positif vers le haut.
 * Revient au neutre (0, 0) quand on relâche.
 */
class JoystickView @JvmOverloads constructor(
    context: Context,
    attrs: AttributeSet? = null,
) : View(context, attrs) {

    var onMove: ((x: Float, y: Float) -> Unit)? = null

    private var knobX = 0f
    private var knobY = 0f
    private var baseRadius = 0f
    private var knobRadius = 0f

    private val basePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(50, 255, 255, 255)
    }
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(110, 255, 255, 255)
        style = Paint.Style.STROKE
        strokeWidth = 4f
    }
    private val knobPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(210, 0, 176, 255)
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        baseRadius = min(w, h) / 2f * 0.72f
        knobRadius = min(w, h) / 2f * 0.30f
    }

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        canvas.drawCircle(cx, cy, baseRadius, basePaint)
        canvas.drawCircle(cx, cy, baseRadius, ringPaint)
        canvas.drawCircle(cx + knobX, cy + knobY, knobRadius, knobPaint)
    }

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE -> {
                var dx = event.x - width / 2f
                var dy = event.y - height / 2f
                val distance = hypot(dx, dy)
                if (distance > baseRadius && distance > 0f) {
                    dx = dx / distance * baseRadius
                    dy = dy / distance * baseRadius
                }
                knobX = dx
                knobY = dy
                onMove?.invoke(dx / baseRadius, -dy / baseRadius)
                invalidate()
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                knobX = 0f
                knobY = 0f
                onMove?.invoke(0f, 0f)
                invalidate()
            }
        }
        return true
    }
}
