"""PySide6 Conversation Editor — external window for game-style dialogue editing.

Launched from Blender via modal operator. Reads/writes directly to Blender
PropertyGroups. Qt event loop pumped by Blender modal timer.
"""

import os
import sys
from pathlib import Path

# Ensure bundled deps directory is on sys.path for PySide6
def _find_deps():
    return str(Path(__file__).parent / "deps")

_deps_dir = _find_deps()
if _deps_dir not in sys.path:
    sys.path.insert(0, _deps_dir)

def bpy_abspath(p):
    """Expand Blender's // path prefix when running inside Blender."""
    try:
        import bpy
        return bpy.path.abspath(p)
    except Exception:
        return p


try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QSplitter, QTreeWidget,
        QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QFormLayout,
        QLabel, QLineEdit, QCheckBox, QFrame, QScrollArea, QMenu,
        QSizePolicy, QPushButton, QComboBox, QAbstractItemView,
        QStackedWidget, QFileDialog, QRadioButton, QButtonGroup,
        QToolButton,
    )
    from PySide6.QtCore import Qt, QSize, Signal, QTimer
    from PySide6.QtGui import QPixmap, QFont, QIcon, QColor, QPalette, QAction
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


# ---------------------------------------------------------------------------
# Stylesheet — dark theme inspired by X-Men Legends UI
# ---------------------------------------------------------------------------

STYLESHEET = """
QMainWindow {
    background-color: #1a1a2e;
}
QSplitter {
    background-color: #1a1a2e;
}
QSplitter::handle {
    background-color: #0f3460;
    width: 3px;
}
QTreeWidget {
    background-color: #16213e;
    color: #d0d0d0;
    border: 1px solid #0f3460;
    font-size: 13px;
    outline: none;
}
QTreeWidget::item {
    padding: 3px 4px;
    border-bottom: 1px solid #1a1a3e;
}
QTreeWidget::item:selected {
    background-color: #0f3460;
    color: #ffffff;
}
QTreeWidget::item:hover {
    background-color: #1a2a5e;
}
QTreeWidget::branch {
    background-color: #16213e;
}
QTreeWidget QHeaderView::section {
    background-color: #0f3460;
    color: #e0c040;
    font-weight: bold;
    padding: 4px;
    border: none;
}
QScrollArea {
    border: none;
    background-color: #1a1a2e;
}
QLabel {
    color: #d0d0d0;
}
QLineEdit {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1px solid #e0c040;
}
QCheckBox {
    color: #d0d0d0;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
QFrame#npc_box {
    background-color: #16213e;
    border: 2px solid #0f3460;
    border-radius: 8px;
    padding: 8px;
}
QFrame#brotherhood_box {
    background-color: #2a1a0a;
    border: 1px solid #804020;
    border-radius: 6px;
    padding: 6px;
}
QFrame#response_frame {
    background-color: #121228;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 6px;
}
QFrame#follow_up_box {
    background-color: #162e16;
    border: 2px solid #1a5e1a;
    border-radius: 8px;
    padding: 8px;
}
QFrame#props_frame {
    background-color: #16213e;
    border: 1px solid #0f3460;
    border-radius: 6px;
    padding: 8px;
}
QPushButton {
    background-color: #0f3460;
    color: #e0c040;
    border: 1px solid #1a4a80;
    border-radius: 4px;
    padding: 4px 12px;
    font-weight: bold;
}
QPushButton:hover {
    background-color: #1a4a80;
}
QPushButton:pressed {
    background-color: #0a2a50;
}
QComboBox {
    background-color: #16213e;
    color: #e8e8e8;
    border: 1px solid #0f3460;
    border-radius: 3px;
    padding: 4px 6px;
}
"""


# ---------------------------------------------------------------------------
# Node type icons (unicode symbols for tree display)
# ---------------------------------------------------------------------------

NODE_ICONS = {
    'START_CONDITION': '\u25B6',   # ▶
    'PARTICIPANT':     '\U0001F464',  # 👤
    'LINE':            '\U0001F4AC',  # 💬
    'RESPONSE':        '\u25CB',   # ○
}

NODE_COLORS = {
    'START_CONDITION': '#80c040',
    'PARTICIPANT':     '#40a0e0',
    'LINE':            '#e0c040',
    'RESPONSE':        '#b0b0b0',
}


# ---------------------------------------------------------------------------
# Helper: get bpy conversation data
# ---------------------------------------------------------------------------

def _get_conv_data(scene, conv_index):
    """Safely get conversation data from Blender scene."""
    if conv_index < 0 or conv_index >= len(scene.mm_conversations):
        return None
    return scene.mm_conversations[conv_index]


# ---------------------------------------------------------------------------
# Main editor window
# ---------------------------------------------------------------------------

class ConversationEditorWindow(QMainWindow):
    """External conversation editor with game-style dialogue preview."""

    def __init__(self, scene, conv_index, hud_cache_dir=None):
        super().__init__()
        self._scene = scene
        self._conv_index = conv_index
        self._hud_cache_dir = hud_cache_dir or str(Path(__file__).parent / "hud_cache")
        self._hud_pixmaps = {}  # cache loaded HUD images
        self._updating = False  # prevent recursion during sync
        self._prop_widgets = {}  # current property widgets

        conv = _get_conv_data(scene, conv_index)
        title = f"Conversation Editor"
        if conv:
            title = f"Conversation: {conv.conv_name}"

        self.setWindowTitle(title)
        self.setMinimumSize(1100, 650)
        self.resize(1280, 720)
        self.setStyleSheet(STYLESHEET)

        # Keep on top of Blender
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        self._build_ui()
        self._populate_tree()

    # ----- UI Construction -----

    def _build_ui(self):
        """Build the toolbar + stacked Edit / Preview pages."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar
        toolbar = QHBoxLayout()

        # Mode toggle
        self._btn_mode_edit = QPushButton("✎ Edit")
        self._btn_mode_preview = QPushButton("▶ Preview")
        for b in (self._btn_mode_edit, self._btn_mode_preview):
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton:checked { background-color: #e0c040; "
                "color: #1a1a2e; font-weight: bold; }")
        self._btn_mode_edit.setChecked(True)
        self._btn_mode_edit.clicked.connect(lambda: self._set_mode(0))
        self._btn_mode_preview.clicked.connect(lambda: self._set_mode(1))
        toolbar.addWidget(self._btn_mode_edit)
        toolbar.addWidget(self._btn_mode_preview)
        toolbar.addSpacing(16)

        self._btn_add_line = QPushButton("+ Line")
        self._btn_add_response = QPushButton("+ Response")
        self._btn_add_followup = QPushButton("+ Follow-up")
        self._btn_duplicate = QPushButton("Duplicate")
        self._btn_move_up = QPushButton("↑")
        self._btn_move_down = QPushButton("↓")
        self._btn_delete = QPushButton("Delete")
        self._btn_add_line.clicked.connect(self._on_add_line)
        self._btn_add_response.clicked.connect(self._on_add_response)
        self._btn_add_followup.clicked.connect(self._on_add_followup)
        self._btn_duplicate.clicked.connect(self._on_duplicate_node)
        self._btn_move_up.clicked.connect(lambda: self._on_move_node(-1))
        self._btn_move_down.clicked.connect(lambda: self._on_move_node(1))
        self._btn_delete.clicked.connect(self._on_delete_node)
        self._btn_move_up.setFixedWidth(32)
        self._btn_move_down.setFixedWidth(32)
        for b in (self._btn_add_line, self._btn_add_response,
                  self._btn_add_followup, self._btn_duplicate,
                  self._btn_move_up, self._btn_move_down, self._btn_delete):
            toolbar.addWidget(b)
        toolbar.addStretch()

        # Export menu
        export_btn = QToolButton()
        export_btn.setText("\U0001F4E6 Export")
        export_btn.setPopupMode(QToolButton.InstantPopup)
        export_menu = QMenu(export_btn)
        export_menu.setStyleSheet(
            "QMenu { background-color: #16213e; color: #d0d0d0;"
            " border: 1px solid #0f3460; }"
            "QMenu::item:selected { background-color: #0f3460; }")
        export_menu.addAction("\U0001F50A Export All Voice WAVs…",
                              self._on_export_all_wavs)
        export_menu.addAction("\U0001F3B5 Export Conversation Mixdown WAV…",
                              self._on_export_mixdown)
        export_menu.addSeparator()
        export_menu.addAction("\U0001F4C4 Export ENGB (compiled)",
                              self._on_export_engb)
        export_btn.setMenu(export_menu)
        toolbar.addWidget(export_btn)

        # Close button
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        toolbar.addWidget(btn_close)

        main_layout.addLayout(toolbar)

        # Splitter
        splitter = QSplitter(Qt.Horizontal)

        # Left: Tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Node", "Info"])
        self._tree.setColumnWidth(0, 280)
        self._tree.setColumnWidth(1, 120)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        self._tree.setMinimumWidth(300)

        # Center: Preview
        preview_scroll = QScrollArea()
        preview_scroll.setWidgetResizable(True)
        self._preview_container = QWidget()
        self._preview_layout = QVBoxLayout(self._preview_container)
        self._preview_layout.setAlignment(Qt.AlignTop)
        preview_scroll.setWidget(self._preview_container)
        preview_scroll.setMinimumWidth(400)

        # Right: Properties
        props_scroll = QScrollArea()
        props_scroll.setWidgetResizable(True)
        self._props_container = QWidget()
        self._props_layout = QVBoxLayout(self._props_container)
        self._props_layout.setAlignment(Qt.AlignTop)
        props_scroll.setWidget(self._props_container)
        props_scroll.setMinimumWidth(280)

        splitter.addWidget(self._tree)
        splitter.addWidget(preview_scroll)
        splitter.addWidget(props_scroll)
        splitter.setSizes([320, 480, 320])

        # Stacked pages: 0 = Edit (three-pane), 1 = Preview (game sim)
        self._stack = QStackedWidget()
        self._stack.addWidget(splitter)
        self._preview_pane = ConversationPreviewPane(self)
        self._stack.addWidget(self._preview_pane)
        main_layout.addWidget(self._stack, 1)

    def _set_mode(self, index):
        """Switch between Edit (0) and Preview (1) pages."""
        self._btn_mode_edit.setChecked(index == 0)
        self._btn_mode_preview.setChecked(index == 1)
        self._stack.setCurrentIndex(index)
        if index == 1:
            self._preview_pane.restart()
        else:
            self._preview_pane.stop()

    # ----- Game paths for audio -----

    def _game_audio_ctx(self):
        """(game_dir, soundfile) for voice decoding, from Map Maker settings."""
        settings = getattr(self._scene, 'mm_settings', None)
        game_dir = ""
        soundfile = ""
        if settings is not None:
            if settings.game_data_dir:
                game_dir = os.path.abspath(bpy_abspath(settings.game_data_dir))
            soundfile = getattr(settings, 'soundfile', "") or ""
        return game_dir, soundfile

    # ----- Export actions -----

    def _current_conv(self):
        return _get_conv_data(self._scene, self._conv_index)

    def _on_export_all_wavs(self):
        conv = self._current_conv()
        if not conv:
            return
        game_dir, soundfile = self._game_audio_ctx()
        if not game_dir or not os.path.isdir(game_dir):
            self._voice_status("Set Game Data Dir first (Map Maker Scene tab)")
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "Export all voice WAVs to folder")
        if not out_dir:
            return
        from .convo_preview import export_all_voice_wavs
        exported, missing = export_all_voice_wavs(
            conv, game_dir, soundfile, out_dir)
        msg = f"Exported {exported} voice WAV(s) to {out_dir}"
        if missing:
            msg += f" ({missing} not found in sound banks)"
        self._voice_status(msg)

    def _on_export_mixdown(self):
        conv = self._current_conv()
        if not conv:
            return
        game_dir, soundfile = self._game_audio_ctx()
        if not game_dir or not os.path.isdir(game_dir):
            self._voice_status("Set Game Data Dir first (Map Maker Scene tab)")
            return
        out_path, _f = QFileDialog.getSaveFileName(
            self, "Export conversation mixdown",
            f"{conv.conv_name}_mixdown.wav", "WAV files (*.wav)")
        if not out_path:
            return
        from .convo_preview import export_conversation_mixdown
        brotherhood = self._preview_pane.is_brotherhood()
        res = export_conversation_mixdown(
            conv, game_dir, soundfile, out_path, brotherhood=brotherhood)
        if res:
            self._voice_status(
                f"Mixdown: {res[0]} lines, {res[1]:.1f}s → {out_path}")
        else:
            self._voice_status("No voice lines could be decoded for mixdown")

    def _on_export_engb(self):
        """Compile this conversation to .engb via the Blender operator."""
        try:
            import bpy
            settings = getattr(self._scene, 'mm_settings', None)
            if settings is None or not settings.output_dir:
                self._voice_status(
                    "Set Output Dir first (Map Maker Scene tab) — ENGB not exported")
                return
            # export operator works on the scene's active conversation index
            self._scene.mm_conversations_index = self._conv_index
            result = bpy.ops.mm.export_conversation()
            if 'FINISHED' in result:
                conv = self._current_conv()
                out = bpy_abspath(settings.output_dir)
                self._voice_status(
                    f"Exported {conv.conv_name}.engb → {out}")
            else:
                self._voice_status(f"ENGB export returned {result}")
        except Exception as e:
            self._voice_status(f"ENGB export failed: {e}")

    # ----- Tree Population -----

    def _populate_tree(self):
        """Build QTreeWidget from conversation PropertyGroup data."""
        self._tree.clear()
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        # Import here to avoid circular at module level
        from .conversation import get_children, parse_speaker_text, get_node_indicators

        # Build tree recursively
        node_items = {}  # node_id -> QTreeWidgetItem

        def _build(parent_item, parent_id):
            children = get_children(conv, parent_id)
            resp_no = 0
            for node in children:
                label = self._get_node_label(node)
                if node.node_type == 'RESPONSE':
                    resp_no += 1
                    label = f"[{resp_no}] {label}"
                info = self._get_node_info(node)

                item = QTreeWidgetItem()
                item.setText(0, label)
                item.setText(1, info)
                item.setData(0, Qt.UserRole, node.node_id)

                # Color based on type
                color = NODE_COLORS.get(node.node_type, '#d0d0d0')
                item.setForeground(0, QColor(color))
                item.setForeground(1, QColor('#808080'))

                if parent_item is None:
                    self._tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)

                node_items[node.node_id] = item
                _build(item, node.node_id)

        _build(None, -1)

        # Expand all
        self._tree.expandAll()

        # Select the node matching Blender's current selection
        if conv.nodes_index >= 0 and conv.nodes_index < len(conv.nodes):
            selected_node = conv.nodes[conv.nodes_index]
            if selected_node.node_id in node_items:
                self._tree.setCurrentItem(node_items[selected_node.node_id])

    def _get_node_label(self, node):
        """Build display label for tree item."""
        from .conversation import parse_speaker_text

        icon = NODE_ICONS.get(node.node_type, '')

        if node.node_type == 'START_CONDITION':
            cond = node.condition_script
            if cond:
                # Show just the filename
                cond = os.path.basename(cond)
            label = f"{icon} Start Condition"
            if cond:
                label += f": {cond}"
            return label

        elif node.node_type == 'PARTICIPANT':
            return f"{icon} {node.participant_name}"

        elif node.node_type == 'LINE':
            speaker, dialogue = parse_speaker_text(node.text)
            if not speaker and not dialogue:
                return f"{icon} (empty line)"
            name = speaker or "NPC"
            text = dialogue[:45] + "..." if len(dialogue) > 45 else dialogue
            return f"{icon} {name}: {text}"

        elif node.node_type == 'RESPONSE':
            text = node.response_text.strip()
            if text == '%BLANK%':
                return f"\u25B7 (auto-advance)"  # ▷
            _, resp = parse_speaker_text(text)
            if not resp:
                resp = text
            resp = resp[:45] + "..." if len(resp) > 45 else resp
            return f"{icon} {resp}"

        return f"{icon} {node.node_type}"

    def _get_node_info(self, node):
        """Build info column text with indicator symbols."""
        from .conversation import get_node_indicators
        ind = get_node_indicators(node)
        parts = []
        if ind['has_sound']:
            parts.append('\U0001F50A')  # 🔊
        if ind['has_end']:
            parts.append('\u2716')  # ✖
        if ind['has_tag_jump']:
            parts.append('\u2192')  # →
        if ind['has_tag_index']:
            parts.append('\U0001F516')  # 🔖
        if ind['has_faction'] == 'xmen':
            parts.append('[X]')
        elif ind['has_faction'] == 'brotherhood':
            parts.append('[B]')
        if ind['has_brotherhood_variant']:
            parts.append('\u0042\u0332')  # B̲
        return ' '.join(parts)

    # ----- Selection Changed -----

    def _on_tree_selection_changed(self, current, previous):
        """Handle tree selection — update preview and properties."""
        if self._updating or current is None:
            return

        node_id = current.data(0, Qt.UserRole)
        if node_id is None:
            return

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        # Sync Blender selection
        from .conversation import find_node_index
        blender_idx = find_node_index(conv, node_id)
        if blender_idx >= 0:
            # Find the flat display index for this node_id
            from .conversation import get_flat_display_order
            display_order = get_flat_display_order(conv)
            for display_idx, (display_node, _depth) in enumerate(display_order):
                if display_node.node_id == node_id:
                    conv.nodes_index = display_idx
                    break

        # Update preview and properties
        self._update_preview(node_id)
        self._update_properties(node_id)

    # ----- Dialogue Preview -----

    def _update_preview(self, node_id):
        """Update the center dialogue preview pane."""
        # Clear existing
        self._clear_layout(self._preview_layout)

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        from .conversation import find_node_by_id, get_conversation_context

        node = find_node_by_id(conv, node_id)
        if not node:
            return

        ctx = get_conversation_context(conv, node)
        mode = ctx['mode']

        if mode == 'line':
            self._draw_line_preview(ctx, conv)
        elif mode == 'response':
            self._draw_response_preview(ctx, conv)
        elif mode == 'start_condition':
            self._draw_start_condition_preview(node)
        elif mode == 'participant':
            self._draw_participant_preview(node)

    def _draw_line_preview(self, ctx, conv):
        """Draw NPC dialogue line preview."""
        # NPC Speech Box
        npc_box = QFrame()
        npc_box.setObjectName("npc_box")
        npc_layout = QVBoxLayout(npc_box)

        # Header: HUD portrait + speaker name + sound icon
        header = QHBoxLayout()

        # HUD portrait
        hud_pixmap = self._get_hud_pixmap(conv.hud_head)
        if hud_pixmap:
            portrait = QLabel()
            portrait.setPixmap(hud_pixmap.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            portrait.setFixedSize(68, 68)
            header.addWidget(portrait)

        # Speaker name
        speaker = ctx.get('speaker', 'NPC')
        name_label = QLabel(speaker or "NPC")
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet("color: #e0c040;")
        header.addWidget(name_label)
        header.addStretch()

        # Sound: clickable play button (live-decodes from the ZSNDPC bank)
        if ctx.get('has_sound'):
            header.addWidget(self._make_play_button(ctx.get('sound_path', '')))

        npc_layout.addLayout(header)

        # Dialogue text
        dialogue = ctx.get('dialogue', '')
        if dialogue:
            text_label = QLabel(dialogue)
            text_label.setWordWrap(True)
            text_label.setStyleSheet("color: #e8e8e8; font-size: 14px; padding: 8px 4px;")
            npc_layout.addWidget(text_label)

        # Brotherhood variant
        dialogue_b = ctx.get('dialogue_b', '')
        if dialogue_b:
            bro_box = QFrame()
            bro_box.setObjectName("brotherhood_box")
            bro_layout = QVBoxLayout(bro_box)

            bro_header = QLabel("\u2694 Brotherhood Variant")  # ⚔
            bro_header.setStyleSheet("color: #c08040; font-weight: bold; font-size: 12px;")
            bro_layout.addWidget(bro_header)

            bro_text = QLabel(dialogue_b)
            bro_text.setWordWrap(True)
            bro_text.setStyleSheet("color: #d0b070; font-size: 13px;")
            bro_layout.addWidget(bro_text)

            npc_layout.addWidget(bro_box)

        self._preview_layout.addWidget(npc_box)

        # Player Choices
        responses = ctx.get('responses', [])
        if responses:
            self._draw_responses_section(responses)

    def _draw_response_preview(self, ctx, conv):
        """Draw response preview: parent NPC speech + response list + follow-up."""
        # Parent NPC speech
        if ctx.get('parent_speaker') or ctx.get('parent_dialogue'):
            parent_box = QFrame()
            parent_box.setObjectName("npc_box")
            parent_layout = QVBoxLayout(parent_box)

            # Header with portrait
            header = QHBoxLayout()
            hud_pixmap = self._get_hud_pixmap(conv.hud_head)
            if hud_pixmap:
                portrait = QLabel()
                portrait.setPixmap(hud_pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                portrait.setFixedSize(52, 52)
                header.addWidget(portrait)

            name_label = QLabel(ctx.get('parent_speaker', 'NPC'))
            name_label.setStyleSheet("color: #e0c040; font-weight: bold; font-size: 14px;")
            header.addWidget(name_label)
            header.addStretch()
            parent_layout.addLayout(header)

            dialogue = ctx.get('parent_dialogue', '')
            if dialogue:
                text_label = QLabel(dialogue)
                text_label.setWordWrap(True)
                text_label.setStyleSheet("color: #c0c0c0; font-size: 13px; padding: 4px;")
                parent_layout.addWidget(text_label)

            self._preview_layout.addWidget(parent_box)

        # Response list
        responses = ctx.get('responses', [])
        if responses:
            self._draw_responses_section(responses)

        # Follow-up NPC speech
        if ctx.get('follow_up_speaker') or ctx.get('follow_up_dialogue'):
            fu_box = QFrame()
            fu_box.setObjectName("follow_up_box")
            fu_layout = QVBoxLayout(fu_box)

            header = QHBoxLayout()
            hud_pixmap = self._get_hud_pixmap(conv.hud_head)
            if hud_pixmap:
                portrait = QLabel()
                portrait.setPixmap(hud_pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                portrait.setFixedSize(52, 52)
                header.addWidget(portrait)

            name_label = QLabel(ctx.get('follow_up_speaker', 'NPC'))
            name_label.setStyleSheet("color: #40c040; font-weight: bold; font-size: 14px;")
            header.addWidget(name_label)
            header.addStretch()

            if ctx.get('has_sound'):
                header.addWidget(
                    self._make_play_button(ctx.get('sound_path', '')))

            fu_layout.addLayout(header)

            dialogue = ctx.get('follow_up_dialogue', '')
            if dialogue:
                text_label = QLabel(dialogue)
                text_label.setWordWrap(True)
                text_label.setStyleSheet("color: #c0e0c0; font-size: 13px; padding: 4px;")
                fu_layout.addWidget(text_label)

            self._preview_layout.addWidget(fu_box)

    def _draw_responses_section(self, responses):
        """Draw the player choices section."""
        resp_frame = QFrame()
        resp_frame.setObjectName("response_frame")
        resp_layout = QVBoxLayout(resp_frame)

        header = QLabel("Player Choices:")
        header.setStyleSheet("color: #a0a0c0; font-weight: bold; font-size: 12px; padding-bottom: 4px;")
        resp_layout.addWidget(header)

        for i, resp in enumerate(responses, 1):
            row = QHBoxLayout()

            # Selection indicator
            if resp.get('is_selected'):
                bullet = '\u25CF'  # ●
                color = '#ffffff'
                bg = 'background-color: #0f3460; border-radius: 4px; padding: 3px 6px;'
            elif resp.get('is_blank'):
                bullet = '\u25B7'  # ▷
                color = '#606060'
                bg = 'padding: 3px 6px;'
            else:
                bullet = '\u25CB'  # ○
                color = '#b8b8b8'
                bg = 'padding: 3px 6px;'

            text = resp.get('text', '')
            resp_label = QLabel(f"{bullet}  {i}. {text}")
            resp_label.setStyleSheet(f"color: {color}; font-size: 13px; {bg}")
            resp_label.setWordWrap(True)
            row.addWidget(resp_label, 1)

            # Indicator icons
            indicators = []
            if resp.get('faction') == 'xmen':
                indicators.append(('[X]', '#4080e0'))
            elif resp.get('faction') == 'brotherhood':
                indicators.append(('[B]', '#c06030'))
            if resp.get('tag_jump'):
                indicators.append(('\u2192', '#40c0c0'))  # →
            if resp.get('is_end'):
                indicators.append(('\u2716', '#e04040'))  # ✖

            for symbol, ind_color in indicators:
                ind_label = QLabel(symbol)
                ind_label.setStyleSheet(f"color: {ind_color}; font-weight: bold; font-size: 13px;")
                ind_label.setFixedWidth(28)
                ind_label.setAlignment(Qt.AlignCenter)
                row.addWidget(ind_label)

            resp_layout.addLayout(row)

        self._preview_layout.addWidget(resp_frame)

    def _draw_start_condition_preview(self, node):
        """Draw start condition info box."""
        box = QFrame()
        box.setObjectName("npc_box")
        layout = QVBoxLayout(box)

        header = QLabel("\u25B6 Start Condition")
        header.setStyleSheet("color: #80c040; font-weight: bold; font-size: 15px;")
        layout.addWidget(header)

        if node.condition_script:
            layout.addWidget(self._info_row("Script:", node.condition_script))
        layout.addWidget(self._info_row("Run Once:", "Yes" if node.run_once else "No"))

        self._preview_layout.addWidget(box)

    def _draw_participant_preview(self, node):
        """Draw participant info box."""
        box = QFrame()
        box.setObjectName("npc_box")
        layout = QVBoxLayout(box)

        header = QLabel("\U0001F464 Participant")
        header.setStyleSheet("color: #40a0e0; font-weight: bold; font-size: 15px;")
        layout.addWidget(header)

        layout.addWidget(self._info_row("Name:", node.participant_name or "default"))

        self._preview_layout.addWidget(box)

    def _info_row(self, label, value):
        """Create a label: value info row."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #808090; font-size: 13px;")
        lbl.setFixedWidth(80)
        val = QLabel(str(value))
        val.setStyleSheet("color: #d0d0d0; font-size: 13px;")
        val.setWordWrap(True)
        h.addWidget(lbl)
        h.addWidget(val, 1)
        return w

    # ----- HUD Portrait Loading -----

    def _get_hud_pixmap(self, hud_name):
        """Load and cache HUD head portrait as QPixmap."""
        if not hud_name:
            return None

        if hud_name in self._hud_pixmaps:
            return self._hud_pixmaps[hud_name]

        png_path = os.path.join(self._hud_cache_dir, f"{hud_name}.png")
        if not os.path.exists(png_path):
            self._hud_pixmaps[hud_name] = None
            return None

        pixmap = QPixmap(png_path)
        if pixmap.isNull():
            self._hud_pixmaps[hud_name] = None
            return None

        self._hud_pixmaps[hud_name] = pixmap
        return pixmap

    # ----- Voice Playback -----

    def _play_voice(self, sound_ref):
        """Live-decode a conversation voice line from its ZSNDPC bank and play it.

        sound_ref = the node's sound_to_play (e.g. "voice/fury/1_stark1_010_013_nf").
        Decoding one line takes ~0.07 s and is cached in the temp dir, so this
        is safe to call on every click.
        """
        if not sound_ref:
            return
        try:
            from .voice_audio import resolve_voice_wav, play_wav
            settings = getattr(self._scene, 'mm_settings', None)
            game_dir = ""
            soundfile = ""
            if settings is not None:
                game_dir = os.path.abspath(
                    bpy_abspath(settings.game_data_dir)) if settings.game_data_dir else ""
                soundfile = getattr(settings, 'soundfile', "") or ""
            if not game_dir or not os.path.isdir(game_dir):
                self._voice_status(f"Set Game Data Dir first (Scene tab) — can't find {sound_ref}")
                return
            res = resolve_voice_wav(game_dir, sound_ref, soundfile=soundfile)
            if res:
                play_wav(res[0])
                self._voice_status(f"\U0001F50A {os.path.basename(sound_ref)} ({res[1]:.1f}s)")
            else:
                self._voice_status(f"Voice not found in sound banks: {sound_ref}")
        except Exception as e:
            self._voice_status(f"Playback failed: {e}")

    def _voice_status(self, msg):
        try:
            self.statusBar().showMessage(msg, 6000)
        except Exception:
            print(msg)

    def _make_play_button(self, sound_ref):
        """Small ▶ button that plays a voice line reference."""
        btn = QPushButton('\U0001F50A ▶')
        btn.setToolTip(f"Play: {sound_ref}")
        btn.setFixedWidth(56)
        btn.clicked.connect(lambda _=False, s=sound_ref: self._play_voice(s))
        return btn

    # ----- Properties Panel -----

    def _update_properties(self, node_id):
        """Update the right-side properties panel for selected node."""
        self._clear_layout(self._props_layout)
        self._prop_widgets = {}

        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        from .conversation import find_node_by_id
        node = find_node_by_id(conv, node_id)
        if not node:
            return

        # Title
        props_frame = QFrame()
        props_frame.setObjectName("props_frame")
        form = QVBoxLayout(props_frame)

        type_label = QLabel(f"Node: {node.node_type}")
        type_label.setStyleSheet("color: #e0c040; font-weight: bold; font-size: 14px; padding-bottom: 6px;")
        form.addWidget(type_label)

        # Build fields based on type
        if node.node_type == 'START_CONDITION':
            self._add_text_field(form, node, 'condition_script', "Condition Script")
            self._add_bool_field(form, node, 'run_once', "Run Once")

        elif node.node_type == 'PARTICIPANT':
            self._add_text_field(form, node, 'participant_name', "Name")

        elif node.node_type == 'LINE':
            self._add_text_field(form, node, 'text', "Text")
            self._add_text_field(form, node, 'sound_to_play', "Sound File")
            if getattr(node, 'sound_to_play', ''):
                play_btn = QPushButton('\U0001F50A Play Voice Line')
                play_btn.clicked.connect(
                    lambda _=False, n=node:
                        self._play_voice(getattr(n, 'sound_to_play', '')))
                form.addWidget(play_btn)
            self._add_text_field(form, node, 'text_b', "Text (Brotherhood)")
            self._add_text_field(form, node, 'sound_to_play_b', "Sound (Brotherhood)")
            self._add_text_field(form, node, 'tag_index', "Tag Index")

        elif node.node_type == 'RESPONSE':
            self._add_text_field(form, node, 'response_text', "Response Text")
            self._add_text_field(form, node, 'chosen_script', "Chosen Script")
            self._add_text_field(form, node, 'script_file', "Script File")
            self._add_bool_field(form, node, 'conversation_end', "End Conversation")
            self._add_text_field(form, node, 'tag_jump', "Tag Jump")
            self._add_text_field(form, node, 'tag_index', "Tag Index")
            self._add_bool_field(form, node, 'only_if_xman', "X-Men Only")
            self._add_bool_field(form, node, 'only_if_brotherhood', "Brotherhood Only")

        self._props_layout.addWidget(props_frame)

        # Add stretch at bottom
        self._props_layout.addStretch()

    def _add_text_field(self, layout, node, prop_name, label):
        """Add a text input field that syncs to PropertyGroup."""
        lbl = QLabel(label)
        lbl.setStyleSheet("color: #808090; font-size: 12px; padding-top: 4px;")
        layout.addWidget(lbl)

        edit = QLineEdit()
        edit.setText(getattr(node, prop_name, ''))
        edit.textChanged.connect(
            lambda text, n=node, p=prop_name: self._on_prop_changed(n, p, text)
        )
        layout.addWidget(edit)
        self._prop_widgets[prop_name] = edit

    def _add_bool_field(self, layout, node, prop_name, label):
        """Add a checkbox field that syncs to PropertyGroup."""
        cb = QCheckBox(label)
        cb.setChecked(getattr(node, prop_name, False))
        cb.stateChanged.connect(
            lambda state, n=node, p=prop_name: self._on_prop_bool_changed(n, p, state)
        )
        layout.addWidget(cb)
        self._prop_widgets[prop_name] = cb

    def _on_prop_changed(self, node, prop_name, value):
        """Handle text property change — write to Blender PropertyGroup."""
        if self._updating:
            return
        self._updating = True
        try:
            setattr(node, prop_name, value)
            # Refresh tree label for this node
            self._refresh_current_tree_item(node)
            # Refresh preview
            self._update_preview(node.node_id)
        finally:
            self._updating = False

    def _on_prop_bool_changed(self, node, prop_name, state):
        """Handle bool property change."""
        if self._updating:
            return
        self._updating = True
        try:
            setattr(node, prop_name, bool(state))
            self._refresh_current_tree_item(node)
            self._update_preview(node.node_id)
        finally:
            self._updating = False

    def _refresh_current_tree_item(self, node):
        """Refresh the label/info of the currently selected tree item."""
        item = self._tree.currentItem()
        if item and item.data(0, Qt.UserRole) == node.node_id:
            item.setText(0, self._get_node_label(node))
            item.setText(1, self._get_node_info(node))

    # ----- Node Operations -----

    def _on_add_line(self):
        """Add a new LINE node under the selected node."""
        self._add_node('LINE')

    def _on_add_response(self):
        """Add a new RESPONSE node under the selected node."""
        self._add_node('RESPONSE')

    def _add_node(self, node_type):
        """Add a new child node under the selected node."""
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        item = self._tree.currentItem()
        if not item:
            return

        parent_id = item.data(0, Qt.UserRole)
        if parent_id is None:
            return

        from .conversation import allocate_node_id, get_next_sort_order

        new_node = conv.nodes.add()
        new_node.node_id = allocate_node_id(conv)
        new_node.parent_id = parent_id
        new_node.node_type = node_type
        new_node.sort_order = get_next_sort_order(conv, parent_id)

        if node_type == 'LINE':
            new_node.text = "%NPC%: "
        elif node_type == 'RESPONSE':
            new_node.response_text = "New response"

        # Rebuild tree
        self._populate_tree()

    def _on_add_followup(self):
        """Add a follow-up LINE under the selected RESPONSE (or line)."""
        self._add_node('LINE')

    def _on_duplicate_node(self):
        """Duplicate the selected node (same parent, props copied)."""
        conv = _get_conv_data(self._scene, self._conv_index)
        item = self._tree.currentItem()
        if not conv or not item:
            return
        node_id = item.data(0, Qt.UserRole)
        from .conversation import (find_node_by_id, allocate_node_id,
                                   get_next_sort_order)
        src = find_node_by_id(conv, node_id)
        if src is None:
            return
        new_node = conv.nodes.add()
        # copy every property except identity/tree fields
        skip = {'node_id', 'sort_order'}
        for prop in src.bl_rna.properties:
            pid = prop.identifier
            if prop.is_readonly or pid in skip or pid == 'rna_type':
                continue
            try:
                setattr(new_node, pid, getattr(src, pid))
            except Exception:
                pass
        new_node.node_id = allocate_node_id(conv)
        new_node.parent_id = src.parent_id
        new_node.sort_order = get_next_sort_order(conv, src.parent_id)
        self._populate_tree()

    def _on_move_node(self, direction):
        """Move the selected node up/down among its siblings."""
        conv = _get_conv_data(self._scene, self._conv_index)
        item = self._tree.currentItem()
        if not conv or not item:
            return
        node_id = item.data(0, Qt.UserRole)
        from .conversation import find_node_by_id, get_children
        node = find_node_by_id(conv, node_id)
        if node is None:
            return
        sibs = get_children(conv, node.parent_id)
        idx = next((i for i, s in enumerate(sibs)
                    if s.node_id == node.node_id), -1)
        swap = idx + direction
        if idx < 0 or swap < 0 or swap >= len(sibs):
            return
        # normalize sort orders then swap the two
        for i, s in enumerate(sibs):
            s.sort_order = i
        sibs[idx].sort_order, sibs[swap].sort_order = swap, idx
        self._populate_tree()
        # keep the moved node selected
        self._select_node_in_tree(node_id)

    def _select_node_in_tree(self, node_id):
        def _walk(item):
            if item.data(0, Qt.UserRole) == node_id:
                self._tree.setCurrentItem(item)
                return True
            for i in range(item.childCount()):
                if _walk(item.child(i)):
                    return True
            return False
        for i in range(self._tree.topLevelItemCount()):
            if _walk(self._tree.topLevelItem(i)):
                break

    def _on_delete_node(self):
        """Delete the selected node and its descendants."""
        conv = _get_conv_data(self._scene, self._conv_index)
        if not conv:
            return

        item = self._tree.currentItem()
        if not item:
            return

        node_id = item.data(0, Qt.UserRole)
        if node_id is None:
            return

        from .conversation import remove_node_and_descendants
        remove_node_and_descendants(conv, node_id)

        # Rebuild tree
        self._populate_tree()

    # ----- Context Menu -----

    def _on_tree_context_menu(self, pos):
        """Show right-click context menu on tree."""
        item = self._tree.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #16213e;
                color: #d0d0d0;
                border: 1px solid #0f3460;
            }
            QMenu::item:selected {
                background-color: #0f3460;
            }
        """)

        add_line_action = menu.addAction("\U0001F4AC Add Line")
        add_resp_action = menu.addAction("\u25CB Add Response")
        menu.addSeparator()
        delete_action = menu.addAction("\u2716 Delete Node")

        action = menu.exec_(self._tree.viewport().mapToGlobal(pos))
        if action == add_line_action:
            self._on_add_line()
        elif action == add_resp_action:
            self._on_add_response()
        elif action == delete_action:
            self._on_delete_node()

    # ----- Layout Helpers -----

    def _clear_layout(self, layout):
        """Remove all widgets from a layout."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    # ----- Keyboard Shortcuts -----

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        # Preview mode: Space advances, 1-9 pick choices, Esc back to edit
        if self._stack.currentIndex() == 1:
            if self._preview_pane.handle_key(event.key()):
                return
            if event.key() == Qt.Key_Escape:
                self._set_mode(0)
                return
            super().keyPressEvent(event)
            return
        if event.modifiers() == Qt.ControlModifier:
            if event.key() == Qt.Key_N:
                self._on_add_line()
                return
            elif event.key() == Qt.Key_R:
                self._on_add_response()
                return
            elif event.key() == Qt.Key_P:
                self._set_mode(1)
                return
            elif event.key() == Qt.Key_W:
                self.close()
                return
        elif event.key() == Qt.Key_Delete:
            self._on_delete_node()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# In-game preview pane
# ---------------------------------------------------------------------------

class ConversationPreviewPane(QWidget):
    """Game-flow simulator: plays the conversation like in-game.

    Portrait + speaker + typewriter text + live-decoded voice, numbered
    player choices (keyboard 1-9), %BLANK% auto-advance, tag jumps,
    X-Men/Brotherhood faction variants, back/restart navigation and an
    optional auto-advance that follows the voice line's real duration.
    """

    TYPE_INTERVAL_MS = 14      # typewriter speed (chars appear 2 per tick)
    AUTO_ADVANCE_PAD_MS = 500  # pause after voice ends before auto-advance

    def __init__(self, window):
        super().__init__()
        self._win = window
        self._current = None       # current LINE node
        self._history = []         # node_id history for Back
        self._generation = 0       # cancels stale timers
        self._full_text = ""
        self._shown_chars = 0
        self._ended = False

        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_tick)

        self._build_ui()

    # ----- UI -----

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 12, 24, 16)

        # Control bar
        bar = QHBoxLayout()
        self._btn_restart = QPushButton("⟲ Restart")
        self._btn_back = QPushButton("◀ Back")
        self._btn_restart.clicked.connect(self.restart)
        self._btn_back.clicked.connect(self.go_back)
        bar.addWidget(self._btn_restart)
        bar.addWidget(self._btn_back)
        bar.addSpacing(20)

        bar.addWidget(QLabel("Faction:"))
        self._rb_xmen = QRadioButton("Heroes")
        self._rb_bro = QRadioButton("Brotherhood")
        self._rb_xmen.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self._rb_xmen)
        grp.addButton(self._rb_bro)
        self._rb_xmen.toggled.connect(lambda _c: self._refaction())
        bar.addWidget(self._rb_xmen)
        bar.addWidget(self._rb_bro)
        bar.addSpacing(20)

        self._cb_voice = QCheckBox("\U0001F50A Voice")
        self._cb_voice.setChecked(True)
        self._cb_auto = QCheckBox("Auto-advance")
        self._cb_auto.setChecked(False)
        bar.addWidget(self._cb_voice)
        bar.addWidget(self._cb_auto)
        bar.addStretch()

        self._breadcrumb = QLabel("")
        self._breadcrumb.setStyleSheet("color: #607090; font-size: 11px;")
        bar.addWidget(self._breadcrumb)
        root.addLayout(bar)

        root.addStretch(1)

        # Dialogue frame (game look)
        self._frame = QFrame()
        self._frame.setStyleSheet(
            "QFrame { background-color: rgba(8, 12, 40, 235);"
            " border: 2px solid #e0c040; border-radius: 6px; }")
        fl = QVBoxLayout(self._frame)
        fl.setContentsMargins(16, 12, 16, 12)

        header = QHBoxLayout()
        self._portrait = QLabel()
        self._portrait.setFixedSize(84, 84)
        self._portrait.setStyleSheet("border: 1px solid #e0c040;")
        self._portrait.setScaledContents(True)
        header.addWidget(self._portrait)
        header.addSpacing(10)
        self._speaker = QLabel("")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        self._speaker.setFont(f)
        self._speaker.setStyleSheet("color: #e0c040; border: none;")
        header.addWidget(self._speaker)
        header.addStretch()
        fl.addLayout(header)

        self._text = QLabel("")
        self._text.setWordWrap(True)
        self._text.setMinimumHeight(70)
        self._text.setStyleSheet(
            "color: #f0f0f0; font-size: 16px; border: none; padding: 6px;")
        self._text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        fl.addWidget(self._text)
        root.addWidget(self._frame)

        # Choices
        self._choices_box = QVBoxLayout()
        root.addLayout(self._choices_box)
        root.addStretch(2)

    # ----- helpers -----

    def is_brotherhood(self):
        return self._rb_bro.isChecked()

    def _conv(self):
        return _get_conv_data(self._win._scene, self._win._conv_index)

    def stop(self):
        self._generation += 1
        self._type_timer.stop()
        try:
            from .voice_audio import stop_playback
            stop_playback()
        except Exception:
            pass

    # ----- flow -----

    def restart(self):
        from .convo_preview import first_line
        self.stop()
        conv = self._conv()
        self._history = []
        self._ended = False
        if not conv:
            self._show_end("No conversation loaded")
            return
        node = first_line(conv)
        if node is None:
            self._show_end("Conversation has no dialogue lines")
            return
        self._show_line(node)

    def go_back(self):
        self.stop()
        if len(self._history) < 2:
            self.restart()
            return
        self._history.pop()                 # current
        prev_id = self._history.pop()       # previous
        from .conversation import find_node_by_id
        node = find_node_by_id(self._conv(), prev_id)
        if node is not None:
            self._show_line(node)
        else:
            self.restart()

    def _refaction(self):
        """Faction toggled — re-render the current line."""
        if self._current is not None and not self._ended:
            node = self._current
            if self._history:
                self._history.pop()
            self.stop()
            self._show_line(node)

    def _show_line(self, node):
        from .convo_preview import line_speaker_text, line_sound
        self._generation += 1
        gen = self._generation
        self._current = node
        self._ended = False
        self._history.append(node.node_id)
        self._breadcrumb.setText(
            " → ".join(str(n) for n in self._history[-8:]))

        brotherhood = self.is_brotherhood()
        speaker, text = line_speaker_text(node, brotherhood)
        self._speaker.setText(speaker or "NPC")

        # portrait
        conv = self._conv()
        pix = self._win._get_hud_pixmap(conv.hud_head) if conv else None
        if pix:
            self._portrait.setPixmap(pix)
            self._portrait.show()
        else:
            self._portrait.clear()
            self._portrait.hide()

        # typewriter
        self._full_text = text or "…"
        self._shown_chars = 0
        self._text.setText("")
        self._type_timer.start(self.TYPE_INTERVAL_MS)

        # choices (built now; buttons act immediately)
        self._build_choices(node)

        # voice + auto-advance
        voice_dur = 0.0
        if self._cb_voice.isChecked():
            ref = line_sound(node, brotherhood)
            if ref:
                voice_dur = self._play_line_voice(ref)

        if self._cb_auto.isChecked():
            delay = int(max(voice_dur, 1.6) * 1000) + self.AUTO_ADVANCE_PAD_MS
            QTimer.singleShot(delay, lambda: self._auto_advance(gen))

    def _play_line_voice(self, sound_ref):
        """Play a line's voice; returns duration seconds (0 when unavailable)."""
        try:
            from .voice_audio import resolve_voice_wav, play_wav
            game_dir, soundfile = self._win._game_audio_ctx()
            if not game_dir or not os.path.isdir(game_dir):
                return 0.0
            res = resolve_voice_wav(game_dir, sound_ref, soundfile=soundfile)
            if res:
                play_wav(res[0])
                return res[1]
        except Exception as e:
            self._win._voice_status(f"Voice failed: {e}")
        return 0.0

    def _type_tick(self):
        self._shown_chars += 2
        if self._shown_chars >= len(self._full_text):
            self._shown_chars = len(self._full_text)
            self._type_timer.stop()
        self._text.setText(self._full_text[:self._shown_chars])

    def _complete_text(self):
        self._type_timer.stop()
        self._shown_chars = len(self._full_text)
        self._text.setText(self._full_text)

    def _build_choices(self, node):
        from .convo_preview import (advance_from_line, is_blank_response)
        from .conversation import parse_speaker_text
        self._clear_choices()
        conv = self._conv()
        kind, nxt = advance_from_line(conv, node, self.is_brotherhood())
        self._pending = (kind, nxt)

        if kind == 'choices':
            visible = [r for r in nxt if not is_blank_response(r)]
            blanks = [r for r in nxt if is_blank_response(r)]
            for i, resp in enumerate(visible[:9]):
                _s, txt = parse_speaker_text(resp.response_text)
                label = f"{i + 1}.  {txt or resp.response_text}"
                suffix = []
                if resp.conversation_end:
                    suffix.append("✖ ends")
                if resp.tag_jump:
                    suffix.append(f"→ {resp.tag_jump}")
                if suffix:
                    label += f"    [{' , '.join(suffix)}]"
                btn = QPushButton(label)
                btn.setStyleSheet(
                    "QPushButton { text-align: left; padding: 8px 14px;"
                    " background-color: #10305a; color: #a0e0a0;"
                    " border: 1px solid #40c040; font-size: 14px; }"
                    "QPushButton:hover { background-color: #1a4a7a; }")
                btn.clicked.connect(
                    lambda _=False, r=resp: self._choose(r))
                self._choices_box.addWidget(btn)
            if blanks and not visible:
                self._add_continue_button(
                    lambda: self._choose(blanks[0]), "▷  Continue")
        elif kind == 'line':
            self._add_continue_button(
                lambda: self._advance_to(nxt), "▷  Continue")
        else:
            self._add_continue_button(None, "■  End of conversation — ⟲ Restart",
                                      end=True)

    def _add_continue_button(self, cb, label, end=False):
        btn = QPushButton(label)
        color = "#e08080" if end else "#80c0e0"
        btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 8px 14px;"
            f" background-color: #10305a; color: {color};"
            " border: 1px solid #0f3460; font-size: 14px; }"
            "QPushButton:hover { background-color: #1a4a7a; }")
        btn.clicked.connect(cb if cb else self.restart)
        self._choices_box.addWidget(btn)

    def _clear_choices(self):
        while self._choices_box.count():
            item = self._choices_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _choose(self, resp):
        from .convo_preview import resolve_response
        self.stop()
        kind, nxt = resolve_response(self._conv(), resp)
        if kind == 'line' and nxt is not None:
            self._show_line(nxt)
        else:
            self._show_end()

    def _advance_to(self, node):
        self.stop()
        self._show_line(node)

    def _auto_advance(self, gen):
        """Fires after the voice line: follow the single continuation."""
        if gen != self._generation or self._ended:
            return
        kind, nxt = getattr(self, '_pending', ('end', None))
        from .convo_preview import is_blank_response
        if kind == 'line':
            self._advance_to(nxt)
        elif kind == 'choices':
            blanks = [r for r in nxt if is_blank_response(r)]
            visible = [r for r in nxt if not is_blank_response(r)]
            if blanks and not visible:
                self._choose(blanks[0])
        # real choices: wait for the player

    def _show_end(self, message=None):
        self._generation += 1
        self._type_timer.stop()
        self._ended = True
        self._current = None
        self._speaker.setText("")
        self._portrait.clear()
        self._portrait.hide()
        self._text.setText(message or "The conversation has ended.")
        self._clear_choices()
        self._add_continue_button(None, "■  End of conversation — ⟲ Restart",
                                  end=True)

    # ----- keyboard -----

    def handle_key(self, key):
        """Space = complete text / continue; 1-9 = choices; B = faction."""
        if key == Qt.Key_Space:
            if self._shown_chars < len(self._full_text):
                self._complete_text()
                return True
            # single continue button?
            if self._choices_box.count() == 1:
                w = self._choices_box.itemAt(0).widget()
                if w:
                    w.click()
                    return True
            return True
        if Qt.Key_1 <= key <= Qt.Key_9:
            idx = key - Qt.Key_1
            if idx < self._choices_box.count():
                w = self._choices_box.itemAt(idx).widget()
                if w:
                    w.click()
                    return True
        if key == Qt.Key_Backspace:
            self.go_back()
            return True
        return False
