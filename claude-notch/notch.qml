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
    readonly property int fullW:   bridge.panelWidth      // live: menu > width
    readonly property int stripW:  lay.strip
    readonly property int bubbleW: lay.bubble_w
    readonly property int bubbleH: lay.bubble_h
    readonly property int sliverW: lay.sliver_w
    readonly property int sliverH: lay.sliver_h
    readonly property int panelW:  lay.panel_w
    readonly property int chatInset: lay.inset
    readonly property int barH: lay.bar                    // the "+" bar under the terminal

    width:  fullW
    height: bridge.winH

    // ── strings ──────────────────────────────────────────────────────
    readonly property var txt: ({
        en: { title: "Claude Usage", session: "Current session", all: "All models",
              used: "% used", none: "no data", resetIn: "resets in", now: "now",
              chatOpen: "Open chat", chatClose: "Close chat", newSession: "New session",
              continueSession: "Continue last session", project: "Project", window: "Open in a window",
              details: "Show details", refresh: "Refresh", settings: "Settings",
              restart: "Restart notch", quit: "Quit", monitor: "Monitor", width: "Panel width",
              autostart: "Start at login", language: "Language", editConfig: "Edit config file",
              log: "View log", about: "About", back: "Back", halfScreen: "Half of the screen",
              langSystem: "System", version: "Version", repo: "Project page",
              addFiles: "Add files or photos", addFolder: "Add folder", connectors: "Connectors",
              plugins: "Plugins", plusHint: "Add to the conversation", pasteImage: "Paste image from clipboard",
              dropHint: "Drop to add to the conversation", model: "Model", stop: "Stop",
              context: "context", listening: "Listening… click the mic again to finish",
              transcribing: "Transcribing…", cancel: "Cancel" },
        cs: { title: "Claude Usage", session: "Aktuální relace", all: "Všechny modely",
              used: " % využito", none: "žádná data", resetIn: "reset za", now: "teď",
              chatOpen: "Otevřít chat", chatClose: "Zavřít chat", newSession: "Nová relace",
              continueSession: "Pokračovat v poslední", project: "Projekt", window: "Otevřít v okně",
              details: "Zobrazit podrobnosti", refresh: "Obnovit", settings: "Nastavení",
              restart: "Restartovat notch", quit: "Ukončit", monitor: "Monitor", width: "Šířka panelu",
              autostart: "Spouštět po přihlášení", language: "Jazyk", editConfig: "Upravit konfiguraci",
              log: "Zobrazit log", about: "O aplikaci", back: "Zpět", halfScreen: "Polovina obrazovky",
              langSystem: "Podle systému", version: "Verze", repo: "Stránka projektu",
              addFiles: "Přidat soubory nebo fotky", addFolder: "Přidat složku", connectors: "Konektory",
              plugins: "Pluginy", plusHint: "Přidat do konverzace", pasteImage: "Vložit obrázek ze schránky",
              dropHint: "Pusť a přidá se do konverzace", model: "Model", stop: "Zastavit",
              context: "kontext", listening: "Poslouchám… dalším kliknutím na mikrofon ukončíš",
              transcribing: "Přepisuji…", cancel: "Zrušit" }
    })[bridge.lang] || ({})                                // live: menu > language

    // ── state ────────────────────────────────────────────────────────
    readonly property bool chat: bridge.chatOpen
    // The shape follows chatVisual: immediately on open, but on close only after
    // the terminal has faded out — otherwise it would stick out of the collapsing shape.
    property bool chatVisual: false
    onChatChanged: { if (chat) { collapseDelay.stop(); chatVisual = true } else { collapseDelay.restart(); barMenu = "" }; updateBubble() }
    // The bar's popups ("plus", "project", "model"): like the menu they must sit
    // above the terminal while open.
    property string barMenu: ""
    readonly property bool barMenuOpen: barMenu !== ""
    onBarMenuChanged: {
        if (barMenuOpen) { menuOpen = false; if (chat) bridge.raiseNotch() }
        else { lingerHole(); if (chat && !menuOpen) bridge.raiseTerminal() }
    }
    function toggleBarMenu(which) { barMenu = barMenu === which ? "" : which }
    // While a popup is up the container leaves the terminal's rectangle open.
    // Raising the terminal back goes through KWin (two D-Bus round trips), so
    // the hole stays open a little longer — otherwise the container would
    // paint over the terminal for those frames and the chat would blink.
    property bool holeLinger: false
    Timer { id: holeTimer; interval: 600; onTriggered: win.holeLinger = false }
    function lingerHole() { holeLinger = true; holeTimer.restart() }
    Timer { id: collapseDelay; interval: cfg.timing.collapse_delay_ms; onTriggered: win.chatVisual = false }

    // The terminal is parked invisible at click time and revealed the instant the
    // shape covers its rectangle (width past the inset, height past the insets).
    readonly property bool terminalCovered: chatVisual
        && shape.sw >= fullW - lay.pad - 1
        && shape.sh >= height - 2 * chatInset - 1
    onTerminalCoveredChanged: if (terminalCovered) bridge.revealTerminal()

    property bool hover: false
    property bool suppressHover: false     // after a closing click, until the pointer leaves
    property bool pinInfo: false           // "Show details" from the menu keeps the bubble open
    property bool menuOpen: false
    property string menuPage: "main"
    // The bubble stays while the pointer is on the orb or the menu is open.
    // Recomputed explicitly: a short-circuiting binding would not pick up
    // menuOpen as a dependency until some earlier operand had changed.
    property bool showBubble: false
    function updateBubble() {
        showBubble = !chat && (menuOpen || hover || pinInfo || orbHover.hovered)
        bridge.reportState({ chat: chat, chatVisual: chatVisual, hover: hover, menuOpen: menuOpen,
                             pinInfo: pinInfo, showBubble: showBubble, orbHover: orbHover.hovered,
                             menuPage: menuPage })
    }
    onHoverChanged: updateBubble()
    onPinInfoChanged: updateBubble()
    Connections { target: orbHover; function onHoveredChanged() { win.updateBubble() } }
    // The orb only exists together with the bubble (or the chat strip) — never on a bare desktop.
    readonly property bool orbVisible: chat || showBubble
    // How far the shape has grown out of the sliver (0..1). Drives what may be visible:
    // nothing is ever drawn where there is no dark background underneath.
    readonly property real grow: Math.max(0, Math.min(1, (shape.sw - sliverW) / (bubbleW - sliverW)))

    onMenuOpenChanged: {
        if (menuOpen) { menuPage = "main"; barMenu = ""; if (chat) bridge.raiseNotch() }   // the menu must sit above the terminal
        else { pointerWasInside = false; lingerHole(); if (chat && !barMenuOpen) bridge.raiseTerminal() }
        updateBubble()
    }
    Connections {
        target: bridge
        function onMenuRequested() { win.menuOpen = !win.menuOpen }
        function onPlusRequested() { win.toggleBarMenu("plus") }
        function onVoiceChanged() { micGlyph.requestPaint() }
        function onDetailsRequested() { win.pinInfo = !win.pinInfo }
    }

    // ── input region: the window only reacts where something is drawn ──
    // Computed from the *target* geometry of each state (not the animated one),
    // so the mask is not re-sent sixty times a second.
    readonly property var hotRects: {
        var W = width, H = height, r = []
        if (chat) {
            r.push([W - stripW, 0, stripW, H])
            r.push([lay.pad, H - chatInset - lay.pad - barH, W - stripW - lay.pad, barH])
            var pop = activePopup()
            if (pop) r.push([pop.x - 6, pop.y - 6, pop.width + 12, pop.height + 12])
        }
        else if (showBubble || pinInfo) {
            var hw = bubbleW + 10 + panelW + 12, hh = Math.max(bubbleH, 200) + 60
            r.push([W - hw, (H - hh) / 2, hw, hh])
        } else r.push([W - lay.sliver_hot, (H - sliverH - 28) / 2, lay.sliver_hot, sliverH + 28])
        if (orbVisible) r.push([orb.targetCx - 18, orb.targetCy - 18, 36, 36])
        if (menuOpen) {          // one convex region: menu + bubble/strip + orb, so no path between them leaves the window
            var top = Math.min(menu.y - 6, r[0][1]), bottom = Math.max(orb.targetCy + 22, r[0][1] + r[0][3])
            r = [[menu.x - 6, top, W - (menu.x - 6), bottom - top]]
        }
        return r
    }
    onHotRectsChanged: bridge.applyMask(hotRects)

    // ── usage data ───────────────────────────────────────────────────
    property int  fiveHour: -1
    property int  sevenDay: -1
    property real fiveReset: 0
    property real sevenReset: 0
    property real writtenAt: 0
    property real now: Date.now() / 1000
    property int  ctx: -1                  // the panel session's context window, % used
    property string model: ""
    // A pick in the bar shows at once; the feed confirms it on its next write.
    property string modelOverride: ""
    readonly property var u: bridge.usage
    onUChanged: {
        fiveHour = u.fiveHour; sevenDay = u.sevenDay
        fiveReset = u.fiveReset; sevenReset = u.sevenReset
        writtenAt = u.writtenAt; now = Date.now() / 1000
        ctx = u.ctx
        if (u.model !== model) { model = u.model; modelOverride = "" }
        ring.requestPaint()
    }
    // The bar's Stop follows the panel session when the feed can tell it apart,
    // else whatever the ring shows.
    readonly property bool busyHere: bridge.panelActivity === "busy" || (bridge.panelActivity === "" && busy)
    readonly property bool stale: fiveHour < 0 || (now - writtenAt) > cfg.timing.stale_after_s

    // ── live session activity from the hooks: busy → spinning arc, waiting → amber pulse ──
    readonly property string activity: bridge.activity
    readonly property bool busy: activity === "busy"
    readonly property bool waiting: activity === "waiting"
    // Prstenec i procenta jedou přes animovanou ringPct: doroste z 0, přechází plynule.
    property real ringPct: 0
    onFiveHourChanged: ringPct = Math.max(0, fiveHour)
    Behavior on ringPct { NumberAnimation { duration: 850; easing.type: Easing.OutCubic } }
    onRingPctChanged: ring.requestPaint()

    // Klidové dýchání — jemný pulz, ať notch žije i když se nic neděje.
    property real breathe: 1
    SequentialAnimation on breathe {
        loops: Animation.Infinite
        running: win.orbVisible && !win.busy && !win.waiting && !win.stale
        NumberAnimation { to: 1.0;  duration: 1900; easing.type: Easing.InOutSine }
        NumberAnimation { to: 0.72; duration: 1900; easing.type: Easing.InOutSine }
    }

    property real spin: 0
    NumberAnimation on spin { from: 0; to: 360; duration: 1400; loops: Animation.Infinite; running: win.busy }
    property real pulse: 1
    SequentialAnimation on pulse {
        loops: Animation.Infinite; running: win.waiting
        NumberAnimation { to: 0.35; duration: 650; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1.0;  duration: 650; easing.type: Easing.InOutSine }
    }
    onActivityChanged: { actRing.requestPaint(); if (!waiting) pulse = 1 }

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

    Component.onCompleted: { visible = true; updateBubble(); bridge.applyMask(hotRects) }
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
        property color col: win.grow > 0.03 ? pal.background : (win.waiting ? pal.warn : win.colorFor(win.fiveHour))
        property real alpha: win.grow > 0.03 ? 1 : (win.waiting ? win.pulse : (win.stale ? 0.55 : win.breathe))

        Behavior on sw   { NumberAnimation { duration: 360; easing.type: Easing.OutBack; easing.overshoot: 0.55 } }
        Behavior on sh   { NumberAnimation { duration: 380; easing.type: Easing.OutBack; easing.overshoot: 0.65 } }
        Behavior on rad  { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.0 } }
        Behavior on crad { NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 1.0 } }
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
                var x0 = Math.max(0, W - shape.sw)       // overshoot nesmi vyjet za okno
                var t  = Math.max(0, (height - shape.sh) / 2)
                var b  = Math.min(height, t + shape.sh)
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
                // While a popup holds the notch above the terminal, leave the
                // terminal's rectangle open so it stays visible underneath.
                if (win.chat && win.chatVisual && (win.menuOpen || win.barMenuOpen || win.holeLinger)) {
                    var pad = lay.pad, ins = win.chatInset
                    ctx.clearRect(pad, ins + pad, W - win.stripW - pad, height - 2 * (ins + pad) - win.barH)
                }
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            Connections { target: win
                function onMenuOpenChanged() { cv.requestPaint() }
                function onBarMenuChanged() { cv.requestPaint() }
                function onHoleLingerChanged() { cv.requestPaint() }
                function onChatVisualChanged() { cv.requestPaint() } }
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
        Behavior on x     { enabled: win.chat; NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.6 } }
        Behavior on y     { enabled: win.chat; NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.6 } }
        Behavior on width { enabled: win.chat; NumberAnimation { duration: 320; easing.type: Easing.OutBack; easing.overshoot: 0.6 } }

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
                        var f = Math.min(Math.max(win.ringPct, 0), 100) / 100
                        if (f > 0) {
                            ctx.strokeStyle = win.colorFor(win.ringPct)
                            ctx.beginPath()
                            ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * f)
                            ctx.stroke()
                        }
                    }
                }
                onWidthChanged: requestPaint()
            }
            Canvas {              // activity overlay: spinning arc (busy) / pulsing amber ring (waiting)
                id: actRing
                anchors.fill: parent
                visible: win.busy || win.waiting
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset()
                    var cx = width / 2, cy = height / 2, lw = width * 0.085
                    var r = width / 2 - lw / 2 - 1
                    if (r <= 0) return
                    ctx.lineCap = "round"
                    if (win.waiting) {
                        ctx.strokeStyle = pal.warn; ctx.globalAlpha = win.pulse; ctx.lineWidth = lw
                        ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke()
                    } else if (win.busy) {
                        var a = win.spin * Math.PI / 180
                        ctx.strokeStyle = "#ffffff"; ctx.globalAlpha = 0.9; ctx.lineWidth = lw * 0.55
                        ctx.beginPath(); ctx.arc(cx, cy, r - lw * 1.15, a, a + Math.PI * 0.42); ctx.stroke()
                    }
                }
                onWidthChanged: requestPaint()
                Connections {
                    target: win
                    function onSpinChanged()  { if (win.busy) actRing.requestPaint() }
                    function onPulseChanged() { if (win.waiting) actRing.requestPaint() }
                }
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
            text: win.fiveHour >= 0 ? Math.round(win.ringPct) + "%" : "–"
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
        readonly property bool ready: win.showBubble && !win.menuOpen && win.grow > 0.6
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

    // ── input over the shape ─────────────────────────────────────────
    MouseArea {
        id: mainArea
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        onEntered: { if (!win.chat && !win.suppressHover && !win.menuOpen) win.hover = true; bridge.reload() }
        onExited:  { win.hover = false; win.suppressHover = false }
        onPositionChanged: if (!win.chat && !win.suppressHover && !win.menuOpen) win.hover = true
        onClicked: {
            if (win.menuOpen) { win.menuOpen = false; return }
            if (win.pinInfo)  { win.pinInfo = false; return }
            win.hover = false
            if (win.chat) win.suppressHover = true
            bridge.toggle()
        }
    }

    // ── the orb: a ) hook below the notch that curls into a gear on hover; click → menu ──
    Item {
        id: orb
        readonly property int r: 22
        // target (state) position — used for the input mask and the menu anchor
        readonly property real targetCx: win.chat ? win.width - win.stripW / 2
                                        : (win.showBubble ? win.width - 18 : win.width - 16)
        readonly property real targetCy: win.chat ? win.height - win.chatInset - 30
                                        : (win.height + (win.showBubble ? win.bubbleH : win.sliverH)) / 2 + 18
        // drawn position — follows the animated shape
        property real cx: win.chatVisual ? win.width - win.stripW / 2
                                         : (win.showBubble ? win.width - 18 : win.width - 16)
        property real cy: win.chatVisual ? win.height - win.chatInset - 30 : (win.height + shape.sh) / 2 + 18
        Behavior on cx { NumberAnimation { duration: 300; easing.type: Easing.OutQuint } }

        x: cx - r - 10; y: cy - r - 10
        width: 2 * r + 20; height: 2 * r + 20
        opacity: win.orbVisible ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 140 } }
        property bool hot: orbHover.hovered || win.menuOpen
        // 0 = the ) hook, 1 = the gear. The hook curls up into the gear: it rounds
        // off, winds around into a full ring while shrinking toward the gear's
        // spot, then the teeth grow out of the ring with a small twist.
        property real morph: hot ? 1 : 0
        Behavior on morph { NumberAnimation { duration: 420; easing.type: Easing.InOutCubic } }

        Canvas {
            id: orbCv
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var t = orb.morph
                function clamp(v) { return Math.max(0, Math.min(1, v)) }
                function lerp(a, b, k) { return a + (b - a) * k }
                var g = 6
                var Wx = win.width - orb.x                          // screen edge, local
                var bb = (win.height + shape.sh) / 2 - orb.y        // shape bottom, local
                var cr = shape.crad
                var R = orb.r * 0.5                                 // gear radius
                // the hook: the shape's bottom inverted corner, offset outward by g
                var P0x = Wx - cr - g, P0y = bb + g, P1x = Wx - g, P1y = bb + g, P2x = Wx - g, P2y = bb + cr + g
                var hcx = Wx - cr - g, hcy = bb + cr + g, hr = cr + g      // its circle
                // the gear: rim on the hook curve, centred one radius toward the concave side
                var mx = Wx - g - 0.25 * cr, my = bb + g + 0.25 * cr
                var sft = (R + 2) / Math.SQRT2
                var gcx = mx - sft, gcy = my + sft, gr = R * 0.40

                var q = clamp(t / 0.25)                             // bezier hook → circular arc
                var s = clamp((t - 0.1) / 0.9); s = s * s * (3 - 2 * s)   // curl, travel, shrink
                var k = clamp((t - 0.62) / 0.38)                    // teeth
                var ccx = lerp(hcx, gcx, s), ccy = lerp(hcy, gcy, s), rr = lerp(hr, gr, s)
                var sweep = lerp(Math.PI / 2, 2 * Math.PI, s)
                var lw = lerp(4.5, R * 0.26, s)

                function path() {
                    ctx.beginPath()
                    var n = 28
                    for (var i = 0; i <= n; i++) {
                        var u = i / n, v = 1 - u
                        var ax = ccx + rr * Math.cos(-Math.PI / 2 + u * sweep)
                        var ay = ccy + rr * Math.sin(-Math.PI / 2 + u * sweep)
                        var bx = v * v * P0x + 2 * v * u * P1x + u * u * P2x
                        var by = v * v * P0y + 2 * v * u * P1y + u * u * P2y
                        var x = lerp(bx, ax, q), y = lerp(by, ay, q)
                        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                    }
                    if (s > 0.999) ctx.closePath()
                }
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                // The curl stays a dark stroke over the desktop; only once the ring is
                // small and nearly closed does the disc fill in and the stroke turn light.
                var d = clamp((s - 0.55) / 0.45)
                if (d > 0) {
                    ctx.globalAlpha = d; ctx.fillStyle = pal.background
                    ctx.beginPath(); ctx.arc(ccx, ccy, (R + 2) * d, 0, 2 * Math.PI); ctx.fill()
                }
                if (d < 1) {
                    ctx.strokeStyle = "#ffffff"; ctx.globalAlpha = 0.28 * (1 - d); ctx.lineWidth = lw + 3.5; path(); ctx.stroke()
                }
                ctx.lineWidth = lw
                if (d < 1) { ctx.strokeStyle = pal.background; ctx.globalAlpha = (win.stale ? 0.6 : 1) * (1 - d); path(); ctx.stroke() }
                if (d > 0) { ctx.strokeStyle = "#ebebf0"; ctx.globalAlpha = d; path(); ctx.stroke() }
                // teeth grow out of the ring with a little twist
                if (k > 0) {
                    ctx.strokeStyle = "#ebebf0"; ctx.globalAlpha = 1; ctx.lineWidth = R * 0.26
                    var twist = (1 - k) * 0.7
                    for (var j = 0; j < 8; j++) {
                        var a = j * Math.PI / 4 + twist
                        ctx.beginPath()
                        ctx.moveTo(ccx + Math.cos(a) * R * 0.62, ccy + Math.sin(a) * R * 0.62)
                        ctx.lineTo(ccx + Math.cos(a) * lerp(R * 0.62, R * 0.90, k), ccy + Math.sin(a) * lerp(R * 0.62, R * 0.90, k))
                        ctx.stroke()
                    }
                }
            }
        }
        onMorphChanged: orbCv.requestPaint()
        onCyChanged: orbCv.requestPaint()
        Connections { target: shape; function onShChanged() { orbCv.requestPaint() } function onCradChanged() { orbCv.requestPaint() } }
        Connections { target: win; function onStaleChanged() { orbCv.requestPaint() } }

        HoverHandler { id: orbHover }
        TapHandler {
            gesturePolicy: TapHandler.ReleaseWithinBounds     // exclusive grab: the main area must not see this click
            onTapped: win.menuOpen = !win.menuOpen
        }
    }

    // The menu (and pinned details) close when nothing is under the pointer any
    // more — the main area, the orb or the menu — after a short debounce that
    // bridges the hand-over between them.
    readonly property bool pointerInside: mainArea.containsMouse || orbHover.hovered || menuHover.hovered
                                          || barHover.hovered || plusPop.hovered || projectPop.hovered || modelPop.hovered
    // Close on leave only once the pointer has actually been inside — so a menu
    // opened programmatically (a shortcut, `claude-notch menu`) stays up until
    // the user moves onto it and away again, instead of vanishing at once.
    property bool pointerWasInside: false
    onPointerInsideChanged: {
        if (pointerInside) { pointerWasInside = true; leaveTimer.stop() }
        else if (pointerWasInside) leaveTimer.restart()
    }
    Timer { id: leaveTimer; interval: 150; onTriggered: if (!win.pointerInside) { win.menuOpen = false; win.barMenu = ""; win.pinInfo = false } }

    // ── the menu ─────────────────────────────────────────────────────
    function menuItems(page) {
        var T = txt
        switch (page) {
        case "main": return [
            { l: win.chat ? T.chatClose : T.chatOpen, a: function() { bridge.toggle() } },
            { l: T.newSession,      a: function() { bridge.newSession() } },
            { l: T.continueSession, a: function() { bridge.continueSession() } },
            { l: T.project,         sub: "project" },
            { l: T.window,          a: function() { bridge.openWindow() } },
            { sep: true },
            { l: T.details,         a: function() { win.pinInfo = true }, hide: win.chat },
            { l: T.refresh,         a: function() { bridge.reload() } },
            { sep: true },
            { l: T.settings,        sub: "settings" },
            { l: T.about,           sub: "about" },
            { sep: true },
            { l: T.restart,         a: function() { bridge.restart() } },
            { l: T.quit,            a: function() { bridge.quit() } }
        ]
        case "settings": return [
            { l: T.monitor,    sub: "monitor" },
            { l: T.width,      sub: "width" },
            { l: T.autostart,  check: bridge.autostart, a: function() { bridge.setAutostart(!bridge.autostart) }, keep: true },
            { l: T.language,   sub: "language" },
            { sep: true },
            { l: T.editConfig, a: function() { bridge.openConfig() } },
            { l: T.log,        a: function() { bridge.openLog() } }
        ]
        case "project": return bridge.projects.map(function(p) {
            return { l: p.name, check: p.current, a: function() { bridge.setProject(p.path) } } })
        case "monitor": return bridge.outputs.map(function(o) {
            return { l: o.label, check: o.current, a: function() { bridge.setScreen(o.name) } } })
        case "width": return [560, 720, 900, Math.round(Screen.width / 2)].map(function(w, i) {
            return { l: (i === 3 ? T.halfScreen + " (" + w + " px)" : w + " px"),
                     check: bridge.panelWidth === w, a: function() { bridge.setWidth(w) } } })
        case "language": return [["auto", T.langSystem], ["en", "English"], ["cs", "Čeština"]].map(function(x) {
            return { l: x[1], check: bridge.languageSetting === x[0], a: function() { bridge.setLanguage(x[0]) } } })
        case "about": return [
            { l: "Claude Notch " + bridge.version, info: true },
            { l: T.repo, a: function() { bridge.openRepo() } }
        ]
        }
        return []
    }
    readonly property var pageItems: {
        var items = menuItems(menuPage).filter(function(i) { return !i.hide })
        if (menuPage !== "main") items.unshift({ l: "‹  " + txt.back, back: true })
        return items
    }

    Rectangle {
        id: menu
        width: 236
        height: menuCol.implicitHeight + 12
        radius: 16
        color: pal.background
        border.color: Qt.rgba(1, 1, 1, 0.10); border.width: 1
        // beside the bubble (or the chat strip), never over it
        x: win.width - width - (win.chat ? win.stripW : win.bubbleW) - 12
        y: Math.max(8, orb.targetCy + orb.r + 6 - height)
        opacity: win.menuOpen ? 1 : 0
        visible: opacity > 0.01
        transform: Translate { y: win.menuOpen ? 0 : 8
                               Behavior on y { NumberAnimation { duration: 160; easing.type: Easing.OutQuint } } }
        Behavior on opacity { NumberAnimation { duration: 120 } }
        HoverHandler { id: menuHover }

        Column {
            id: menuCol
            x: 6; y: 6
            width: parent.width - 12

            Repeater {
                model: win.pageItems
                delegate: Item {
                    required property var modelData
                    width: menuCol.width
                    height: modelData.sep === true ? 9 : 32

                    Rectangle {      // separator
                        visible: modelData.sep === true
                        anchors.centerIn: parent; width: parent.width - 16; height: 1; color: Qt.rgba(1, 1, 1, 0.08)
                    }
                    readonly property bool active: modelData.sep !== true && modelData.info !== true
                    readonly property bool hot: itemHover.hovered && active
                    Rectangle {      // hover highlight
                        anchors.fill: parent; radius: 10
                        color: "#ffffff"; opacity: parent.hot ? 0.16 : 0
                        Behavior on opacity { NumberAnimation { duration: 90 } }
                    }
                    Rectangle {      // accent bar on the left
                        x: 2; width: 3; radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        height: parent.hot ? parent.height - 12 : 0
                        color: win.colorFor(30)
                        Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutQuad } }
                    }
                    Text {
                        visible: modelData.sep !== true
                        anchors.left: parent.left; anchors.leftMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.l || ""
                        color: modelData.info === true ? "#8e8e93" : (parent.hot ? "#ffffff" : "#d8d8dc")
                        font.pixelSize: 13
                        Behavior on color { ColorAnimation { duration: 90 } }
                    }
                    Text {
                        visible: modelData.sep !== true && (typeof modelData.sub === "string" || modelData.check === true)
                        anchors.right: parent.right; anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: typeof modelData.sub === "string" ? "›" : "✓"
                        color: typeof modelData.sub === "string" ? "#8e8e93" : win.colorFor(30)
                        font.pixelSize: 13; font.bold: true
                    }
                    HoverHandler { id: itemHover; enabled: parent.active }
                    TapHandler {
                        enabled: parent.active
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: {
                            if (modelData.back === true) { win.menuPage = "main"; return }
                            if (typeof modelData.sub === "string") { win.menuPage = modelData.sub; return }
                            if (typeof modelData.a === "function") modelData.a()
                            if (modelData.keep !== true) win.menuOpen = false
                        }
                    }
                }
            }
        }
    }

    // Files dragged onto the panel (the + bar or the strip) become @mentions.
    DropArea {
        id: drop
        anchors.fill: parent
        enabled: win.chat
        keys: ["text/uri-list"]
        onEntered: (d) => { if (!d.hasUrls) d.accepted = false }
        onDropped: (d) => { if (d.hasUrls) { bridge.add(d.urls.join("\n")); d.accept() } }
    }

    // ── the bar under the chat terminal: +, project, model, context, stop ──
    function activePopup() {
        switch (barMenu) {
        case "plus":    return plusPop
        case "project": return projectPop
        case "model":   return modelPop
        }
        return null
    }

    // a pill in the bar; `active` while its popup is up
    component BarChip: Item {
        id: chip
        property string label
        property bool chevron: true
        property bool active: false
        property color tint: "#d8d8dc"
        property real strength: 0.06          // resting fill
        signal tapped()
        readonly property bool hot: chipHover.hovered || active
        height: 26; width: chipRow.implicitWidth + 20
        Rectangle {
            anchors.fill: parent; radius: 13
            color: Qt.rgba(1, 1, 1, chip.hot ? chip.strength + 0.09 : chip.strength)
            border.color: Qt.rgba(1, 1, 1, 0.10); border.width: 1
            Behavior on color { ColorAnimation { duration: 90 } }
        }
        Row {
            id: chipRow
            anchors.centerIn: parent; spacing: 5
            Text {
                text: chip.label; color: chip.hot ? "#ffffff" : chip.tint
                font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                Behavior on color { ColorAnimation { duration: 90 } }
            }
            Text {
                visible: chip.chevron
                text: "▾"; color: "#8e8e93"; font.pixelSize: 11
                anchors.verticalCenter: parent.verticalCenter
                rotation: chip.active ? 180 : 0
                Behavior on rotation { NumberAnimation { duration: 180; easing.type: Easing.OutQuint } }
            }
        }
        HoverHandler { id: chipHover }
        TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: chip.tapped() }
    }

    Item {
        id: plusBar
        x: lay.pad; width: win.width - win.stripW - lay.pad
        y: win.height - win.chatInset - lay.pad - win.barH; height: win.barH
        opacity: win.chat && win.chatVisual ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 160 } }
        HoverHandler { id: barHover }

        Rectangle { x: 6; y: 0; width: parent.width - 12; height: 1; color: Qt.rgba(1, 1, 1, 0.07) }
        Rectangle {
            id: plusBtn
            x: 8; anchors.verticalCenter: parent.verticalCenter
            width: 28; height: 28; radius: 14
            color: Qt.rgba(1, 1, 1, plusHover.hovered || win.barMenu === "plus" || drop.containsDrag ? 0.18 : 0.09)
            border.color: Qt.rgba(1, 1, 1, 0.12); border.width: 1
            Behavior on color { ColorAnimation { duration: 90 } }
            Text {           // the + turns into an × while the popup is up
                anchors.centerIn: parent; anchors.verticalCenterOffset: -1
                text: "+"; color: "#ebebf0"; font.pixelSize: 21; font.weight: Font.Light
                rotation: win.barMenu === "plus" ? 45 : 0
                Behavior on rotation { NumberAnimation { duration: 220; easing.type: Easing.OutBack; easing.overshoot: 0.8 } }
            }
            HoverHandler { id: plusHover }
            TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: win.toggleBarMenu("plus") }
        }
        // the mic: click to dictate, click again to finish (right click cancels)
        Rectangle {
            id: micBtn
            anchors.left: plusBtn.right; anchors.leftMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            width: 28; height: 28; radius: 14
            readonly property bool rec: bridge.voiceState === "recording"
            readonly property bool thinking: bridge.voiceState === "transcribing"
            color: rec ? Qt.rgba(1, 0.27, 0.23, 0.22) : Qt.rgba(1, 1, 1, micHover.hovered ? 0.18 : 0.09)
            border.color: rec ? Qt.rgba(1, 0.27, 0.23, 0.55) : Qt.rgba(1, 1, 1, 0.12); border.width: 1
            opacity: bridge.voiceReady ? 1 : 0.45
            Behavior on color { ColorAnimation { duration: 120 } }
            Rectangle {      // the halo breathes with the input level
                anchors.centerIn: parent
                width: parent.width + 4 + bridge.micLevel * 24; height: width; radius: width / 2
                color: "transparent"; border.color: pal.crit; border.width: 1.5
                opacity: micBtn.rec ? 0.18 + bridge.micLevel * 0.5 : 0
                Behavior on width { NumberAnimation { duration: 60 } }
                Behavior on opacity { NumberAnimation { duration: 120 } }
            }
            Canvas {
                id: micGlyph
                anchors.centerIn: parent; width: 18; height: 18
                RotationAnimation on rotation { running: micBtn.thinking; from: 0; to: 360; duration: 900; loops: Animation.Infinite }
                onRotationChanged: if (!micBtn.thinking && rotation !== 0) rotation = 0
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    var cx = width / 2
                    c.lineCap = "round"; c.lineWidth = 1.7
                    if (micBtn.thinking) {           // a short arc, spun by the animation
                        c.strokeStyle = "#ebebf0"
                        c.beginPath(); c.arc(cx, height / 2, 6, 0, Math.PI * 0.6); c.stroke()
                        return
                    }
                    var col = micBtn.rec ? pal.crit : "#ebebf0"
                    c.strokeStyle = col; c.fillStyle = col
                    c.beginPath(); c.roundedRect(cx - 2.5, 2, 5, 9, 2.5, 2.5); c.fill()          // capsule
                    c.beginPath(); c.arc(cx, 8.5, 5, 0, Math.PI); c.stroke()                     // cradle
                    c.beginPath(); c.moveTo(cx, 13.5); c.lineTo(cx, 16); c.stroke()             // stem
                    c.beginPath(); c.moveTo(cx - 3.5, 16); c.lineTo(cx + 3.5, 16); c.stroke()   // base
                }
            }
            HoverHandler { id: micHover }
            TapHandler { gesturePolicy: TapHandler.ReleaseWithinBounds; onTapped: bridge.voice() }
            TapHandler { acceptedButtons: Qt.RightButton; onTapped: bridge.voiceCancel() }
        }

        // While a file is dragged over, or dictation runs, the project chip gives way to a line of text.
        readonly property bool aside: drop.containsDrag || bridge.voiceState !== ""
        Row {
            anchors.left: micBtn.right; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 10
            opacity: plusBar.aside ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 120 } }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: drop.containsDrag ? (win.txt.dropHint || "")
                    : bridge.voiceState === "recording" ? (win.txt.listening || "")
                    : bridge.voiceState === "transcribing" ? (win.txt.transcribing || "") : ""
                color: "#ebebf0"; font.pixelSize: 12
            }
            BarChip {
                anchors.verticalCenter: parent.verticalCenter
                label: win.txt.cancel || ""; chevron: false; strength: 0.04
                visible: bridge.voiceState === "recording"
                onTapped: bridge.voiceCancel()
            }
        }

        // project: the folder the agent runs in — click to switch (new session there)
        BarChip {
            id: projectChip
            anchors.left: micBtn.right; anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            label: bridge.projectName
            active: win.barMenu === "project"
            opacity: plusBar.aside ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: 120 } }
            onTapped: win.toggleBarMenu("project")
        }

        Row {
            anchors.right: parent.right; anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            opacity: drop.containsDrag ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: 120 } }

            // model: what the panel session runs on — click to switch (`/model`)
            BarChip {
                id: modelChip
                anchors.verticalCenter: parent.verticalCenter
                label: win.modelOverride || win.model || win.txt.model || ""
                active: win.barMenu === "model"
                onTapped: win.toggleBarMenu("model")
            }

            // context window: a small arc in the ring's colours
            Item {
                id: ctxGauge
                anchors.verticalCenter: parent.verticalCenter
                width: ctxRow.implicitWidth; height: 26
                visible: win.ctx >= 0
                property real pct: 0
                Behavior on pct { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                Connections { target: win; function onCtxChanged() { ctxGauge.pct = Math.max(0, win.ctx) } }
                Component.onCompleted: pct = Math.max(0, win.ctx)
                onPctChanged: ctxArc.requestPaint()
                Row {
                    id: ctxRow
                    anchors.verticalCenter: parent.verticalCenter; spacing: 6
                    Canvas {
                        id: ctxArc
                        width: 16; height: 16
                        anchors.verticalCenter: parent.verticalCenter
                        onPaint: {
                            var c = getContext("2d"); c.reset()
                            var cx = width / 2, cy = height / 2, lw = 2.6, r = width / 2 - lw / 2
                            c.lineWidth = lw; c.lineCap = "round"
                            c.strokeStyle = "#2c2c2e"
                            c.beginPath(); c.arc(cx, cy, r, 0, 2 * Math.PI); c.stroke()
                            var f = Math.min(ctxGauge.pct, 100) / 100
                            if (f > 0) {
                                c.strokeStyle = win.colorFor(ctxGauge.pct)
                                c.beginPath(); c.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * f); c.stroke()
                            }
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: Math.round(ctxGauge.pct) + " %"
                        color: "#a5a5aa"; font.pixelSize: 12
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: win.txt.context || ""
                        color: "#6e6e73"; font.pixelSize: 11
                    }
                }
            }

            // stop: only while the panel session is working — Escape into the agent
            BarChip {
                id: stopChip
                anchors.verticalCenter: parent.verticalCenter
                label: "■  " + (win.txt.stop || "")
                chevron: false
                tint: pal.crit; strength: 0.04
                visible: opacity > 0.01
                opacity: win.busyHere ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160 } }
                onTapped: bridge.interrupt()
            }
        }
    }

    // popups above the bar, one per chip
    component BarPopup: Rectangle {
        id: pop
        property string which
        property Item under                    // the chip it opens from
        property var items: []                 // { l, check, a }
        property alias hovered: popHover.hovered
        readonly property bool open: win.barMenu === which
        width: 240
        height: popCol.implicitHeight + 12
        radius: 16
        color: pal.background
        border.color: Qt.rgba(1, 1, 1, 0.10); border.width: 1
        x: { open; var ax = under ? under.mapToItem(win, 0, 0).x - 4 : plusBar.x + 4
             return Math.max(plusBar.x, Math.min(ax, plusBar.x + plusBar.width - width)) }
        y: plusBar.y - height - 4
        opacity: open ? 1 : 0
        visible: opacity > 0.01
        transform: Translate { y: pop.open ? 0 : 8
                               Behavior on y { NumberAnimation { duration: 160; easing.type: Easing.OutQuint } } }
        Behavior on opacity { NumberAnimation { duration: 120 } }
        HoverHandler { id: popHover }

        Column {
            id: popCol
            x: 6; y: 6
            width: parent.width - 12
            Repeater {
                model: pop.items
                delegate: Item {
                    required property var modelData
                    width: popCol.width; height: 32
                    readonly property bool hot: popItemHover.hovered
                    Rectangle {
                        anchors.fill: parent; radius: 10
                        color: "#ffffff"; opacity: parent.hot ? 0.16 : 0
                        Behavior on opacity { NumberAnimation { duration: 90 } }
                    }
                    Rectangle {
                        x: 2; width: 3; radius: 1.5
                        anchors.verticalCenter: parent.verticalCenter
                        height: parent.hot ? parent.height - 12 : 0
                        color: win.colorFor(30)
                        Behavior on height { NumberAnimation { duration: 120; easing.type: Easing.OutQuad } }
                    }
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: 12
                        anchors.right: parent.right; anchors.rightMargin: 30
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.l || ""
                        elide: Text.ElideMiddle
                        color: parent.hot ? "#ffffff" : "#d8d8dc"
                        font.pixelSize: 13
                        Behavior on color { ColorAnimation { duration: 90 } }
                    }
                    Text {
                        visible: modelData.check === true
                        anchors.right: parent.right; anchors.rightMargin: 12
                        anchors.verticalCenter: parent.verticalCenter
                        text: "✓"; color: win.colorFor(30); font.pixelSize: 13; font.bold: true
                    }
                    HoverHandler { id: popItemHover }
                    TapHandler {
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: { win.barMenu = ""; modelData.a() }
                    }
                }
            }
        }
    }

    BarPopup {
        id: plusPop
        which: "plus"; under: plusBtn
        items: [
            { l: win.txt.addFiles,   a: function() { bridge.addFiles() } },
            { l: win.txt.addFolder,  a: function() { bridge.addFolder() } },
            { l: win.txt.pasteImage, a: function() { bridge.pasteImage() } },
            { l: win.txt.connectors, a: function() { bridge.sendCommand("/mcp") } },
            { l: win.txt.plugins,    a: function() { bridge.sendCommand("/plugin") } }
        ]
    }
    BarPopup {
        id: projectPop
        which: "project"; under: projectChip
        items: bridge.projects.map(function(p) {
            return { l: p.name, check: p.current, a: function() { bridge.setProject(p.path) } } })
    }
    BarPopup {
        id: modelPop
        which: "model"; under: modelChip
        items: bridge.models.map(function(m) {
            return { l: m.name, check: m.current,
                     a: function() { win.modelOverride = m.name; bridge.setModel(m.id) } } })
    }
}
