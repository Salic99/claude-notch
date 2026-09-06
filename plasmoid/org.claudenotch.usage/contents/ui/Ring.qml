import QtQuick
import org.kde.kirigami as Kirigami

Item {
    id: ring
    property int value: -1          // 0-100, -1 = unavailable
    property string caption: ""
    property bool dimmed: false

    implicitWidth: Kirigami.Units.iconSizes.small * 1.6
    implicitHeight: implicitWidth

    function colorFor(v) {
        if (v < 0) return Kirigami.Theme.disabledTextColor
        if (v < 50) return "#72c68a"
        if (v < 80) return "#d7a84f"
        return "#e06c8b"
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        opacity: ring.dimmed ? 0.35 : 1.0
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2, cy = height / 2
            var lw = Math.max(2, width * 0.14)
            var r  = Math.min(width, height) / 2 - lw / 2 - 1
            if (r <= 0) return
            ctx.lineWidth = lw
            ctx.lineCap = "round"
            ctx.strokeStyle = Kirigami.Theme.disabledTextColor
            ctx.globalAlpha = 0.25
            ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke()
            if (ring.value >= 0) {
                ctx.globalAlpha = 1.0
                ctx.strokeStyle = ring.colorFor(ring.value)
                var frac = Math.min(Math.max(ring.value, 0), 100) / 100
                if (frac > 0) {
                    ctx.beginPath()
                    ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * frac)
                    ctx.stroke()
                }
            }
        }
    }

    onValueChanged: canvas.requestPaint()
    onDimmedChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()

    Text {
        anchors.centerIn: parent
        text: ring.caption
        color: Kirigami.Theme.textColor
        opacity: ring.dimmed ? 0.4 : 0.75
        font.pixelSize: Math.max(6, parent.height * 0.32)
        font.bold: true
    }
}
