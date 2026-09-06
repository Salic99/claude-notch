import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PC3
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.extras as PlasmaExtras
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property int  fiveHour:  -1
    property int  sevenDay:  -1
    property real fiveReset:  0
    property real sevenReset: 0
    property real writtenAt:  0
    property string modelName: ""
    property real now: Date.now() / 1000

    readonly property bool hasData: fiveHour >= 0 || sevenDay >= 0
    readonly property bool stale: !hasData || (now - writtenAt) > 1200

    // Shell access: reads the usage feed (XMLHttpRequest on file:// is unreliable
    // inside plasmoids) and talks to the notch.
    readonly property string feedCmd: "cat \"${XDG_CACHE_HOME:-$HOME/.cache}/claude-usage.json\""
    readonly property string toggleCmd: "\"$HOME/.local/bin/claude-notch\" toggle"

    P5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: function(source, data) {
            if (source === root.feedCmd) root.parse(data["stdout"] || "")
            disconnectSource(source)
        }
        function run(cmd) { connectSource(cmd) }
    }

    function parse(text) {
        now = Date.now() / 1000
        try {
            var d = JSON.parse(text)
            var rl = d.rate_limits || {}
            fiveHour   = rl.five_hour ? Math.round(rl.five_hour.used_percentage) : -1
            sevenDay   = rl.seven_day ? Math.round(rl.seven_day.used_percentage) : -1
            fiveReset  = rl.five_hour ? (rl.five_hour.resets_at || 0) : 0
            sevenReset = rl.seven_day ? (rl.seven_day.resets_at || 0) : 0
            writtenAt  = d._at || 0
            modelName  = (d.model && d.model.display_name) ? d.model.display_name : ""
        } catch (e) { /* feed missing or half-written: keep previous values */ }
    }
    function load() { exec.run(feedCmd) }
    function toggleAgent() { exec.run(toggleCmd) }

    function fmtSpan(sec) {
        if (sec <= 0) return "now"
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
        if (h >= 24) return Math.floor(h / 24) + " d " + (h % 24) + " h"
        return h > 0 ? (h + " h " + m + " min") : (Math.max(1, m) + " min")
    }
    function fmtReset(ts) { return ts ? fmtSpan(ts - now) : "—" }
    function fmtAge()     { return writtenAt ? fmtSpan(now - writtenAt) : "—" }

    Component.onCompleted: load()
    Timer { interval: 15000; running: true; repeat: true; onTriggered: root.load() }
    Timer { interval: 30000; running: true; repeat: true; onTriggered: root.now = Date.now() / 1000 }

    Plasmoid.status: hasData && !stale ? PlasmaCore.Types.ActiveStatus
                                       : PlasmaCore.Types.PassiveStatus

    toolTipMainText: "Claude" + (modelName ? " · " + modelName : "")
    toolTipSubText: (stale
            ? "No fresh data — Claude Code is not running."
            : "5 h:    " + (fiveHour >= 0 ? fiveHour + " %" : "—") + "   ·   resets in " + fmtReset(fiveReset) + "\n"
            + "7 days: " + (sevenDay >= 0 ? sevenDay + " %" : "—") + "   ·   resets in " + fmtReset(sevenReset))
        + "\n\nClick: open the agent   ·   Middle click: details"

    preferredRepresentation: compactRepresentation

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: "Open the agent"
            icon.name: "utilities-terminal"
            onTriggered: root.toggleAgent()
        },
        PlasmaCore.Action {
            text: "Refresh"
            icon.name: "view-refresh"
            onTriggered: root.load()
        }
    ]

    compactRepresentation: MouseArea {
        id: compact
        implicitWidth: layout.implicitWidth + Kirigami.Units.smallSpacing * 2
        implicitHeight: Kirigami.Units.iconSizes.small
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        hoverEnabled: true
        onClicked: function(mouse) {
            if (mouse.button === Qt.MiddleButton) root.expanded = !root.expanded
            else root.toggleAgent()
        }
        property real ringSize: Math.min(compact.height * 0.95, Kirigami.Units.iconSizes.medium)

        RowLayout {
            id: layout
            anchors.centerIn: parent
            spacing: Kirigami.Units.smallSpacing
            Ring { value: root.fiveHour; caption: "5h"; dimmed: root.stale
                   Layout.preferredWidth: compact.ringSize; Layout.preferredHeight: compact.ringSize }
            Ring { value: root.sevenDay; caption: "7d"; dimmed: root.stale
                   Layout.preferredWidth: compact.ringSize; Layout.preferredHeight: compact.ringSize }
        }
    }

    fullRepresentation: PlasmaExtras.Representation {
        Layout.minimumWidth:  Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.largeSpacing

            PlasmaExtras.Heading { level: 3; text: "Claude" + (root.modelName ? " · " + root.modelName : "") }

            Repeater {
                model: [
                    { t: "Current session (5 h)", v: root.fiveHour, r: root.fiveReset },
                    { t: "All models (7 days)",   v: root.sevenDay, r: root.sevenReset }
                ]
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.largeSpacing
                    Ring { value: modelData.v; dimmed: root.stale
                           Layout.preferredWidth: Kirigami.Units.iconSizes.large
                           Layout.preferredHeight: Kirigami.Units.iconSizes.large }
                    ColumnLayout {
                        spacing: 0
                        Layout.fillWidth: true
                        PC3.Label { text: modelData.t; font.bold: true }
                        PC3.Label {
                            opacity: 0.7
                            text: (modelData.v >= 0 ? modelData.v + " % used" : "no data")
                                + (modelData.r ? "   ·   resets in " + root.fmtReset(modelData.r) : "")
                        }
                    }
                }
            }

            Item { Layout.fillHeight: true }

            PC3.Button {
                Layout.fillWidth: true
                icon.name: "utilities-terminal"
                text: "Open the agent"
                onClicked: { root.toggleAgent(); root.expanded = false }
            }

            PC3.Label {
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                opacity: 0.6
                wrapMode: Text.WordWrap
                text: root.stale ? "Data refreshes only while Claude Code is running."
                                 : "Updated " + root.fmtAge() + " ago"
            }
        }
    }
}
