import QtQuick
import QtQuick.Window

// One fixed-size transparent surface. Everything below morphs inside it.
Window {
    id: win
    visible: false
    color: "transparent"
    title: "Claude Notch"
    flags: Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
           | Qt.WindowDoesNotAcceptFocus

    // ── layout & colours come from Python (config.toml) ─────────────
    readonly property var lay: cfg.layout
    readonly property var pal: cfg.colors
    readonly property int fullW:   lay.width
    readonly property int stripW:  lay.strip
    readonly property int bubbleW: lay.bubble_w
    readonly property int bubbleH: lay.bubble_h
    readonly property int sliverW: lay.sliver_w
    readonly property int sliverH: lay.sliver_h
    readonly property int panelW:  lay.panel_w
    readonly property int chatInset: lay.inset

    width:  fullW
    height: bridge.winH

    // ── strings ──────────────────────────────────────────────────────
    readonly property var txt: ({
        en: { title: "Claude Usage", session: "Current session", all: "All models",
              used: "% used", none: "no data", resetIn: "resets in", now: "now" },
        cs: { title: "Claude Usage", session: "Aktuální relace", all: "Všechny modely",
              used: " % využito", none: "žádná data", resetIn: "reset za", now: "teď" }
    })[cfg.ui.language] || ({})

    // ── state ────────────────────────────────────────────────────────
    readonly property bool chat: bridge.chatOpen
    // The shape follows chatVisual: immediately on open, but on close only after
    // the terminal has faded out — otherwise it would stick out of the collapsing shape.
    property bool chatVisual: false
    onChatChanged: { if (chat) { collapseDelay.stop(); chatVisual = true } else collapseDelay.restart() }
    Timer { id: collapseDelay; interval: cfg.timing.collapse_delay_ms; onTriggered: win.chatVisual = false }

    // The terminal is parked invisible at click time and revealed the instant the
    // shape covers its rectangle (width past the inset, height past the insets).
    readonly property bool terminalCovered: chatVisual
        && shape.sw >= fullW - lay.pad - 1
        && shape.sh >= height - 2 * chatInset - 1
    onTerminalCoveredChanged: if (terminalCovered) bridge.revealTerminal()

    property bool hover: false
    property bool suppressHover: false     // after a closing click, until the pointer leaves
    readonly property bool showBubble: hover && !chat
    // How far the shape has grown out of the sliver (0..1). Drives what may be visible:
    // nothing is ever drawn where there is no dark background underneath.
    readonly property real grow: Math.max(0, Math.min(1, (shape.sw - sliverW) / (bubbleW - sliverW)))

    // ── usage data ───────────────────────────────────────────────────
    property int  fiveHour: -1
    property int  sevenDay: -1
    property real fiveReset: 0
    property real sevenReset: 0
    property real writtenAt: 0
    property real now: Date.now() / 1000
    readonly property var u: bridge.usage
    onUChanged: {
        fiveHour = u.fiveHour; sevenDay = u.sevenDay
        fiveReset = u.fiveReset; sevenReset = u.sevenReset
        writtenAt = u.writtenAt; now = Date.now() / 1000
        ring.requestPaint()
    }
    readonly property bool stale: fiveHour < 0 || (now - writtenAt) > cfg.timing.stale_after_s

    function colorFor(v) {
        if (v < 0)  return pal.none
        if (v < 50) return pal.ok
        if (v < 80) return pal.warn
        return pal.crit
    }
    function fmtSpan(sec) {
        if (sec <= 0) return txt.now
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
        if (h >= 24) return Math.floor(h / 24) + " d " + (h % 24) + " h"
        return h > 0 ? (h + " h " + m + " m") : (Math.max(1, m) + " min")
    }

    Component.onCompleted: visible = true
    Timer { interval: 30000; running: true; repeat: true; onTriggered: win.now = Date.now() / 1000 }

    // ── the starburst: the real mark if the installer extracted one from a locally
    //    installed Claude icon (nothing is shipped in the repo), else drawn procedurally ──
    component Starburst: Item {
        property color tint: "#ffffff"
        Image {
            anchors.fill: parent
            visible: glyphUrl !== ""
            source: glyphUrl
            sourceSize: Qt.size(96, 96)
            smooth: true
        }
        Canvas {
            anchors.fill: parent
            visible: glyphUrl === ""
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var cx = width / 2, cy = height / 2, R = Math.min(width, height) / 2
                var rays = [1, .58, .86, .55, .95, .6, .82, .57, 1, .58, .86, .55, .95, .6, .82, .57]
                ctx.strokeStyle = parent.tint; ctx.lineCap = "round"; ctx.lineWidth = Math.max(1.2, R * 0.19)
                for (var i = 0; i < rays.length; i++) {
                    var a = -Math.PI / 2 + i * 2 * Math.PI / rays.length
                    var r0 = R * 0.14, r1 = R * rays[i]
                    ctx.beginPath()
                    ctx.moveTo(cx + Math.cos(a) * r0, cy + Math.sin(a) * r0)
                    ctx.lineTo(cx + Math.cos(a) * r1, cy + Math.sin(a) * r1)
                    ctx.stroke()
                }
            }
            onWidthChanged: requestPaint()
        }
    }

    // ── container: normal corners on the left, inverted ones at the screen edge ──
    Item {
        id: shape
        anchors.fill: parent

        property real sw:  win.chatVisual ? win.fullW : (win.showBubble ? win.bubbleW : win.sliverW)
        property real sh:  win.chatVisual ? (win.height - 2 * win.chatInset)
                                          : (win.showBubble ? win.bubbleH : win.sliverH)
        property real rad:  win.chatVisual ? 20 : (win.showBubble ? 24 : win.sliverW / 2)
        property real crad: win.chatVisual ? 26 : (win.showBubble ? 20 : 4)
        // No colour animation: the sliver tint switches to the dark fill the moment
        // the shape leaves sliver size, so nothing is ever drawn on a green bubble.
        property color col: win.grow > 0.03 ? pal.background : win.colorFor(win.fiveHour)
        property real alpha: (win.stale && !win.chatVisual && !win.showBubble) ? 0.55 : 1

        Behavior on sw   { NumberAnimation { duration: 300; easing.type: Easing.OutQuint } }
        Behavior on sh   { NumberAnimation { duration: 300; easing.type: Easing.OutQuint } }
        Behavior on rad  { NumberAnimation { duration: 260; easing.type: Easing.OutQuad } }
        Behavior on crad { NumberAnimation { duration: 260; easing.type: Easing.OutQuad } }
        Behavior on alpha{ NumberAnimation { duration: 200 } }

        onSwChanged:    cv.requestPaint()
        onShChanged:    cv.requestPaint()
        onRadChanged:   cv.requestPaint()
        onCradChanged:  cv.requestPaint()
        onColChanged:   cv.requestPaint()
        onAlphaChanged: cv.requestPaint()

        Canvas {
            id: cv
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var W = width
                var x0 = W - shape.sw
                var t  = (height - shape.sh) / 2
                var b  = t + shape.sh
                var r  = Math.min(shape.rad, shape.sw / 2, shape.sh / 2)
                var cr = Math.min(shape.crad, shape.sw)

                ctx.globalAlpha = shape.alpha
                ctx.fillStyle = shape.col
                ctx.beginPath()
                ctx.moveTo(W, t - cr)
                ctx.quadraticCurveTo(W, t, W - cr, t)      // inverted corner, top
                ctx.lineTo(x0 + r, t)
                ctx.quadraticCurveTo(x0, t, x0, t + r)     // rounded corner, top-left
                ctx.lineTo(x0, b - r)
                ctx.quadraticCurveTo(x0, b, x0 + r, b)     // rounded corner, bottom-left
                ctx.lineTo(W - cr, b)
                ctx.quadraticCurveTo(W, b, W, b + cr)      // inverted corner, bottom
                ctx.closePath()
                ctx.fill()
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
        }
    }

    // ── badge: starburst + usage ring + percentage ───────────────────
    Item {
        id: badge
        width: win.chatVisual ? 34 : 50
        height: width + 20
        // Emerges only once most of the dark bubble is underneath it.
        opacity: { var g = Math.max(0, (win.grow - 0.45) / 0.55); return g * g }
        // Hard gate: the badge may fly faster than the shape grows, so it is drawn
        // only while its rectangle lies inside the shape.
        readonly property bool insideShape: x >= win.width - shape.sw + 2
            && y >= (win.height - shape.sh) / 2 + 2
            && y + height <= (win.height + shape.sh) / 2 - 2
        visible: opacity > 0.01 && insideShape

        x: win.width - (win.chatVisual ? win.stripW : win.bubbleW) / 2 - width / 2
        y: win.chatVisual ? win.chatInset + 22 : (win.height - height) / 2

        // Animate the flight only into the chat; on close snap, or the badge
        // would hang over the wallpaper while the shape is already collapsing.
        Behavior on x     { enabled: win.chat; NumberAnimation { duration: 260; easing.type: Easing.OutQuint } }
        Behavior on y     { enabled: win.chat; NumberAnimation { duration: 260; easing.type: Easing.OutQuint } }
        Behavior on width { enabled: win.chat; NumberAnimation { duration: 260; easing.type: Easing.OutQuint } }

        Item {
            id: ringBox
            width: parent.width; height: parent.width
            anchors.top: parent.top

            Canvas {
                id: ring
                anchors.fill: parent
                opacity: win.stale ? 0.45 : 1
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset()
                    var cx = width / 2, cy = height / 2, lw = width * 0.085
                    var r = width / 2 - lw / 2 - 1
                    if (r <= 0) return
                    ctx.lineWidth = lw; ctx.lineCap = "round"
                    ctx.strokeStyle = "#2c2c2e"
                    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke()
                    if (win.fiveHour >= 0) {
                        var f = Math.min(Math.max(win.fiveHour, 0), 100) / 100
                        if (f > 0) {
                            ctx.strokeStyle = win.colorFor(win.fiveHour)
                            ctx.beginPath()
                            ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * f)
                            ctx.stroke()
                        }
                    }
                }
                onWidthChanged: requestPaint()
            }
            Starburst {
                anchors.centerIn: parent
                width: parent.width * 0.5; height: width
                opacity: win.stale ? 0.45 : 1
            }
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: ringBox.bottom
            anchors.topMargin: 4
            text: win.fiveHour >= 0 ? win.fiveHour + "%" : "–"
            color: win.chatVisual ? "#8e8e93" : "#ffffff"
            opacity: win.stale ? 0.45 : 1
            font.pixelSize: win.chatVisual ? 10 : 16
            font.bold: true
        }
    }

    // ── details panel on hover (not in chat) ─────────────────────────
    Rectangle {
        id: info
        width: win.panelW
        height: col.implicitHeight + 32
        radius: 18
        color: pal.background
        anchors.verticalCenter: parent.verticalCenter
        x: win.width - win.bubbleW - 10 - width
        readonly property bool ready: win.showBubble && win.grow > 0.6
        opacity: ready ? 1 : 0
        visible: opacity > 0.01
        transform: Translate { x: info.ready ? 0 : 20
                               Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutQuint } } }
        Behavior on opacity { NumberAnimation { duration: 110 } }

        Column {
            id: col
            x: 16; y: 16
            width: parent.width - 32
            spacing: 12

            Row {
                spacing: 8
                Starburst { width: 18; height: 18; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: txt.title; color: "#ffffff"
                    font.pixelSize: 15; font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Repeater {
                model: [
                    { t: txt.session, v: win.fiveHour, r: win.fiveReset },
                    { t: txt.all,     v: win.sevenDay, r: win.sevenReset }
                ]
                delegate: Column {
                    required property var modelData
                    width: col.width
                    spacing: 5
                    Row {
                        width: parent.width
                        Text {
                            text: parent.parent.modelData.t
                            color: "#f2f2f2"; font.pixelSize: 12; width: parent.width / 2
                        }
                        Text {
                            text: parent.parent.modelData.r
                                  ? txt.resetIn + " " + win.fmtSpan(parent.parent.modelData.r - win.now) : ""
                            color: "#8e8e93"; font.pixelSize: 11
                            width: parent.width / 2; horizontalAlignment: Text.AlignRight
                        }
                    }
                    Rectangle {
                        width: parent.width; height: 6; radius: 3; color: "#2c2c2e"
                        Rectangle {
                            width: parent.parent.modelData.v > 0
                                   ? Math.max(6, parent.width * Math.min(parent.parent.modelData.v, 100) / 100) : 0
                            height: parent.height; radius: 3
                            color: win.colorFor(parent.parent.modelData.v)
                            Behavior on width { NumberAnimation { duration: 300 } }
                        }
                    }
                    Text {
                        text: parent.modelData.v >= 0 ? parent.modelData.v + txt.used : txt.none
                        color: "#8e8e93"; font.pixelSize: 11
                    }
                }
            }
        }
    }

    // ── input ────────────────────────────────────────────────────────
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        onEntered: { if (!win.chat && !win.suppressHover) win.hover = true; bridge.reload() }
        onExited:  { win.hover = false; win.suppressHover = false }
        onPositionChanged: if (!win.chat && !win.suppressHover) win.hover = true
        onClicked: { win.hover = false; if (win.chat) win.suppressHover = true; bridge.toggle() }
    }

    onShowBubbleChanged: bridge.setExpanded(showBubble)
}
