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
              used: "% used", none: "no data", resetIn: "resets in", now: "now",
              chatOpen: "Open chat", chatClose: "Close chat", newSession: "New session",
              continueSession: "Continue last session", project: "Project", window: "Open in a window",
              details: "Show details", refresh: "Refresh", settings: "Settings",
              restart: "Restart notch", quit: "Quit", monitor: "Monitor", width: "Panel width",
              autostart: "Start at login", language: "Language", editConfig: "Edit config file",
              log: "View log", about: "About", back: "Back", halfScreen: "Half of the screen",
              langSystem: "System", version: "Version", repo: "Project page" },
        cs: { title: "Claude Usage", session: "Aktuální relace", all: "Všechny modely",
              used: " % využito", none: "žádná data", resetIn: "reset za", now: "teď",
              chatOpen: "Otevřít chat", chatClose: "Zavřít chat", newSession: "Nová relace",
              continueSession: "Pokračovat v poslední", project: "Projekt", window: "Otevřít v okně",
              details: "Zobrazit podrobnosti", refresh: "Obnovit", settings: "Nastavení",
              restart: "Restartovat notch", quit: "Ukončit", monitor: "Monitor", width: "Šířka panelu",
              autostart: "Spouštět po přihlášení", language: "Jazyk", editConfig: "Upravit konfiguraci",
              log: "Zobrazit log", about: "O aplikaci", back: "Zpět", halfScreen: "Polovina obrazovky",
              langSystem: "Podle systému", version: "Verze", repo: "Stránka projektu" }
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
    property bool pinInfo: false           // "Show details" from the menu keeps the bubble open
    property bool menuOpen: false
    property string menuPage: "main"
    // The bubble stays while the pointer is on the orb or the menu is open.
    readonly property bool showBubble: (hover || pinInfo || orbArea.containsMouse || menuOpen) && !chat
    // The orb only exists together with the bubble (or the chat strip) — never on a bare desktop.
    readonly property bool orbVisible: chat || showBubble
    // How far the shape has grown out of the sliver (0..1). Drives what may be visible:
    // nothing is ever drawn where there is no dark background underneath.
    readonly property real grow: Math.max(0, Math.min(1, (shape.sw - sliverW) / (bubbleW - sliverW)))

    onMenuOpenChanged: {
        if (menuOpen) { menuPage = "main"; if (chat) bridge.raiseNotch() }   // the menu must sit above the terminal
        else if (chat) bridge.raiseTerminal()
    }
    Connections { target: bridge; function onMenuRequested() { win.menuOpen = !win.menuOpen } }

    // ── input region: the window only reacts where something is drawn ──
    // Computed from the *target* geometry of each state (not the animated one),
    // so the mask is not re-sent sixty times a second.
    readonly property var hotRects: {
        var W = width, H = height, r = []
        if (chat) r.push([W - stripW, 0, stripW, H])
        else if (showBubble || pinInfo) {
            var hw = bubbleW + 10 + panelW + 12, hh = Math.max(bubbleH, 200) + 60
            r.push([W - hw, (H - hh) / 2, hw, hh])
        } else r.push([W - lay.sliver_hot, (H - sliverH - 28) / 2, lay.sliver_hot, sliverH + 28])
        if (orbVisible) r.push([orb.targetCx - 18, orb.targetCy - 18, 36, 36])
        if (menuOpen) r.push([menu.x - 6, menu.y - 6, menu.width + 12, (orb.targetCy + 22) - menu.y + 6])   // down to the orb, no gap
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

    Component.onCompleted: { visible = true; bridge.applyMask(hotRects) }
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

    // ── the orb: an arc below the notch; a gear on hover; click → menu ──
    Item {
        id: orb
        readonly property int r: 14
        // target (state) position — used for the input mask and the menu anchor
        readonly property real targetCx: win.chat ? win.width - win.stripW / 2
                                        : (win.showBubble ? win.width - win.bubbleW / 2 : win.width - 16)
        readonly property real targetCy: win.chat ? win.height - win.chatInset - 30
                                        : (win.height + (win.showBubble ? win.bubbleH : win.sliverH)) / 2 + 24
        // drawn position — follows the animated shape
        property real cx: win.chatVisual ? win.width - win.stripW / 2
                                         : (win.showBubble ? win.width - win.bubbleW / 2 : win.width - 16)
        property real cy: win.chatVisual ? win.height - win.chatInset - 30 : (win.height + shape.sh) / 2 + 24
        Behavior on cx { NumberAnimation { duration: 300; easing.type: Easing.OutQuint } }

        x: cx - r - 4; y: cy - r - 4
        width: 2 * r + 8; height: 2 * r + 8
        opacity: win.orbVisible ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 140 } }
        property bool hot: orbArea.containsMouse || win.menuOpen

        Canvas {
            id: orbCv
            anchors.fill: parent
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var R = orb.r, cx = width / 2, cy = height / 2
                if (orb.hot) {
                    ctx.fillStyle = pal.background
                    ctx.beginPath(); ctx.arc(cx, cy, R + 2, 0, 2 * Math.PI); ctx.fill()
                    ctx.strokeStyle = "#ebebf0"; ctx.lineCap = "round"
                    ctx.lineWidth = R * 0.26
                    ctx.beginPath(); ctx.arc(cx, cy, R * 0.40, 0, 2 * Math.PI); ctx.stroke()
                    for (var i = 0; i < 8; i++) {          // gear teeth
                        var a = i * Math.PI / 4
                        ctx.beginPath()
                        ctx.moveTo(cx + Math.cos(a) * R * 0.62, cy + Math.sin(a) * R * 0.62)
                        ctx.lineTo(cx + Math.cos(a) * R * 0.90, cy + Math.sin(a) * R * 0.90)
                        ctx.stroke()
                    }
                } else {                                    // resting arc
                    ctx.lineCap = "round"
                    ctx.strokeStyle = "#ffffff"; ctx.globalAlpha = 0.28; ctx.lineWidth = 7
                    ctx.beginPath(); ctx.arc(cx - 6, cy + 3, R - 1, -Math.PI * 0.5, Math.PI * 0.12); ctx.stroke()   // ╮ hook: in from the left, down at the edge
                    ctx.strokeStyle = pal.background; ctx.globalAlpha = win.stale ? 0.6 : 1; ctx.lineWidth = 4.2
                    ctx.beginPath(); ctx.arc(cx - 6, cy + 3, R - 1, -Math.PI * 0.5, Math.PI * 0.12); ctx.stroke()   // ╮ hook: in from the left, down at the edge
                }
            }
        }
        onHotChanged: orbCv.requestPaint()
        Connections { target: win; function onStaleChanged() { orbCv.requestPaint() } }

        MouseArea {
            id: orbArea
            anchors.fill: parent
            hoverEnabled: true
            onClicked: win.menuOpen = !win.menuOpen
        }
    }

    // A cooperative hover handler sees the pointer anywhere in the window, even
    // over MouseAreas that took hover from the big one. The menu closes only
    // when the pointer has really left the window for a moment.
    Item {
        anchors.fill: parent
        z: 1000
        HoverHandler {
            id: winHover
            onHoveredChanged: if (hovered) leaveTimer.stop(); else leaveTimer.restart()
        }
    }
    Timer {
        id: leaveTimer
        interval: 280
        onTriggered: if (!winHover.hovered) { win.menuOpen = false; win.pinInfo = false }
    }

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
        x: win.width - width - 10
        y: Math.max(8, orb.targetCy - orb.r - 10 - height)
        opacity: win.menuOpen ? 1 : 0
        visible: opacity > 0.01
        transform: Translate { y: win.menuOpen ? 0 : 8
                               Behavior on y { NumberAnimation { duration: 160; easing.type: Easing.OutQuint } } }
        Behavior on opacity { NumberAnimation { duration: 120 } }

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
                    readonly property bool hot: itemArea.containsMouse && modelData.sep !== true && modelData.info !== true
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
                    MouseArea {
                        id: itemArea
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: modelData.sep !== true && modelData.info !== true
                        onClicked: {
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
}
