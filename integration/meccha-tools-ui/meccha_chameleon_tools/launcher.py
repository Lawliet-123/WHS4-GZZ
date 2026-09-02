import sys

from PyQt5.QtWidgets import (
    QApplication,
    QPushButton,
    QLabel,
    QCheckBox,
    QLineEdit,
    QTextEdit,
    QTabBar,
    QFrame,
)

from meccha_chameleon_tools.game_session import (
    launch_game,
)

from meccha_chameleon_tools.module_selector import (
    ModuleSelector,
)

from meccha_chameleon_tools.module_runner import (
    ModuleRunner,
    build_runner_summary,
)


# ============================================================
# Integrated test window
# ============================================================

class MecchaToolsWindow(
    ModuleSelector
):

    def __init__(self):

        self.module_runner = (
            ModuleRunner()
        )

        super().__init__()

        self._update_apply_button()

        self._force_large_ui()

        # 전체 UI 자체도 크게
        self.resize(
            1600,
            1000
        )

        self.setMinimumSize(
            1400,
            900
        )

    # ========================================================
    # Apply button
    # ========================================================

    def _update_apply_button(self):

        buttons = self.findChildren(
            QPushButton
        )

        for button in buttons:

            if (
                button.text()
                == "Prepare Selected"
            ):

                button.setText(
                    "Apply Selected (Test)"
                )

                break

    # ========================================================
    # Force font helper
    # ========================================================

    def _set_font_style(
        self,
        widget,
        size,
        bold=False,
        family=None
    ):

        style = (
            widget.styleSheet()
            + f"font-size: {size}px;"
        )

        if bold:

            style += (
                "font-weight: bold;"
            )

        if family:

            style += (
                f'font-family: "{family}";'
            )

        widget.setStyleSheet(
            style
        )

    # ========================================================
    # Force entire UI large
    # ========================================================

    def _force_large_ui(self):
        """
        기존 QSS 크기와 상관없이
        각 위젯에 직접 큰 글씨를 적용함.
        """

        # ----------------------------------------------------
        # Generic labels
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel
        ):

            self._set_font_style(
                label,
                22
            )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        for button in self.findChildren(
            QPushButton
        ):

            self._set_font_style(
                button,
                22,
                bold=True
            )

            button.setMinimumHeight(
                52
            )

        # ----------------------------------------------------
        # Checkboxes
        # ----------------------------------------------------

        for checkbox in self.findChildren(
            QCheckBox
        ):

            self._set_font_style(
                checkbox,
                22
            )

        # ----------------------------------------------------
        # Line edits
        # ----------------------------------------------------

        for line_edit in self.findChildren(
            QLineEdit
        ):

            self._set_font_style(
                line_edit,
                22
            )

            line_edit.setMinimumHeight(
                50
            )

        # ----------------------------------------------------
        # Text edits
        # ----------------------------------------------------

        for text_edit in self.findChildren(
            QTextEdit
        ):

            self._set_font_style(
                text_edit,
                22
            )

        # ----------------------------------------------------
        # Tabs
        # ----------------------------------------------------

        for tab_bar in self.findChildren(
            QTabBar
        ):

            tab_bar.setStyleSheet(
                tab_bar.styleSheet()
                + """
                QTabBar::tab {
                    font-size: 24px;
                    padding: 16px 30px;
                    min-width: 130px;
                    min-height: 35px;
                }
                """
            )

        # ----------------------------------------------------
        # Main title
        # ----------------------------------------------------

        title = self.findChild(
            QLabel,
            "title"
        )

        if title:

            self._set_font_style(
                title,
                40,
                bold=True
            )

        # ----------------------------------------------------
        # Subtitle
        # ----------------------------------------------------

        subtitle = self.findChild(
            QLabel,
            "subtitle"
        )

        if subtitle:

            self._set_font_style(
                subtitle,
                22
            )

        # ----------------------------------------------------
        # Module count
        # ----------------------------------------------------

        module_count = self.findChild(
            QLabel,
            "moduleCount"
        )

        if module_count:

            self._set_font_style(
                module_count,
                22,
                bold=True
            )

        # ----------------------------------------------------
        # Module section
        # ----------------------------------------------------

        section_title = self.findChild(
            QLabel,
            "sectionTitle"
        )

        if section_title:

            self._set_font_style(
                section_title,
                24,
                bold=True
            )

        # ----------------------------------------------------
        # Module names
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "moduleName"
        ):

            self._set_font_style(
                label,
                24,
                bold=True
            )

        # ----------------------------------------------------
        # Module descriptions
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "moduleSmallDescription"
        ):

            self._set_font_style(
                label,
                18
            )

        # ----------------------------------------------------
        # Module rows
        # ----------------------------------------------------

        for frame in self.findChildren(
            QFrame
        ):

            if (
                frame.objectName()
                == "moduleRow"
            ):

                frame.setFixedHeight(
                    96
                )

        # ----------------------------------------------------
        # Status badges
        # ----------------------------------------------------

        status_names = [
            "statusReady",
            "statusPrepared",
            "statusIdle",
            "statusPending",
        ]

        for status_name in status_names:

            for label in self.findChildren(
                QLabel,
                status_name
            ):

                self._set_font_style(
                    label,
                    17,
                    bold=True
                )

        # ----------------------------------------------------
        # Dashboard title
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "dashboardBoxTitle"
        ):

            self._set_font_style(
                label,
                20,
                bold=True
            )

        # ----------------------------------------------------
        # Dashboard value
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "dashboardBoxValue"
        ):

            self._set_font_style(
                label,
                36,
                bold=True
            )

        # ----------------------------------------------------
        # Dashboard boxes
        # ----------------------------------------------------

        dashboard_names = [
            "dashboardReady",
            "dashboardPrepared",
            "dashboardIdle",
            "dashboardUnavailable",
        ]

        for frame in self.findChildren(
            QFrame
        ):

            if (
                frame.objectName()
                in dashboard_names
            ):

                frame.setFixedHeight(
                    90
                )

        # ----------------------------------------------------
        # Detail title
        # ----------------------------------------------------

        detail_title = self.findChild(
            QLabel,
            "detailTitle"
        )

        if detail_title:

            self._set_font_style(
                detail_title,
                34,
                bold=True
            )

        # ----------------------------------------------------
        # Detail description
        # ----------------------------------------------------

        detail_description = (
            self.findChild(
                QLabel,
                "detailDescription"
            )
        )

        if detail_description:

            self._set_font_style(
                detail_description,
                22
            )

        # ----------------------------------------------------
        # Detail status
        # ----------------------------------------------------

        detail_status_names = [
            "detailStatusReady",
            "detailStatusPrepared",
            "detailStatusIdle",
            "detailStatusPending",
        ]

        for status_name in (
            detail_status_names
        ):

            for label in self.findChildren(
                QLabel,
                status_name
            ):

                self._set_font_style(
                    label,
                    19,
                    bold=True
                )

        # ----------------------------------------------------
        # Overview section headings
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "detailSection"
        ):

            self._set_font_style(
                label,
                23,
                bold=True
            )

        # ----------------------------------------------------
        # Overview values
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "detailValue"
        ):

            self._set_font_style(
                label,
                22
            )

        # ----------------------------------------------------
        # Implementation
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "implementationText"
        ):

            self._set_font_style(
                label,
                22
            )

        # ----------------------------------------------------
        # Settings labels
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "settingsLabel"
        ):

            self._set_font_style(
                label,
                22
            )

        # ----------------------------------------------------
        # Settings checkbox
        # ----------------------------------------------------

        for checkbox in (
            self.findChildren(
                QCheckBox,
                "settingsCheck"
            )
        ):

            self._set_font_style(
                checkbox,
                22
            )

        # ----------------------------------------------------
        # Settings messages
        # ----------------------------------------------------

        for label in self.findChildren(
            QLabel,
            "settingsMessage"
        ):

            self._set_font_style(
                label,
                20
            )

        # ----------------------------------------------------
        # Logs
        # ----------------------------------------------------

        log_view = self.findChild(
            QTextEdit,
            "logView"
        )

        if log_view:

            self._set_font_style(
                log_view,
                24,
                family="Consolas"
            )

        # ----------------------------------------------------
        # Selected count
        # ----------------------------------------------------

        selected_count = (
            self.findChild(
                QLabel,
                "selectedCount"
            )
        )

        if selected_count:

            self._set_font_style(
                selected_count,
                22,
                bold=True
            )

        # ----------------------------------------------------
        # Bottom status
        # ----------------------------------------------------

        status_label = self.findChild(
            QLabel,
            "statusLabel"
        )

        if status_label:

            self._set_font_style(
                status_label,
                40,
                bold=True
            )

            status_label.setMinimumHeight(
                70
            )

        # ----------------------------------------------------
        # Left panel width
        # ----------------------------------------------------

        for frame in self.findChildren(
            QFrame
        ):

            if (
                frame.minimumWidth()
                == 385
                and
                frame.maximumWidth()
                == 385
            ):

                frame.setFixedWidth(
                    520
                )

    # ========================================================
    # Apply selected
    # ========================================================

    def prepare_selected(self):

        selected_ids = (
            self.get_selected_modules()
        )

        if not selected_ids:

            super().prepare_selected()

            return

        # 기존 PREPARED
        super().prepare_selected()

        # Test backend
        results = (
            self.module_runner
            .run_selected(
                selected_ids
            )
        )

        # Logs
        for result in results:

            if (
                result.status
                == "TEST_ACTIVE"
            ):

                self.workspace.add_log(
                    result.module_id,
                    (
                        "TEST BACKEND ACTIVE: "
                        + result.message
                    ),
                )

            else:

                self.workspace.add_log(
                    result.module_id,
                    (
                        "TEST BACKEND: "
                        + result.message
                    ),
                    level="WARN",
                )

        # Bottom status
        self.status_label.setText(
            build_runner_summary(
                results
            )
        )

        # Refresh log
        if (
            self.active_module_id
            in selected_ids
        ):

            self.detail_panel.refresh_logs()

    # ========================================================
    # Reset selected
    # ========================================================

    def reset_selected(self):

        selected_ids = (
            self.get_selected_modules()
        )

        if not selected_ids:

            super().reset_selected()

            return

        results = (
            self.module_runner
            .stop_selected(
                selected_ids
            )
        )

        for result in results:

            self.workspace.add_log(
                result.module_id,
                (
                    "TEST BACKEND STOPPED: "
                    + result.message
                ),
            )

        super().reset_selected()

        if (
            self.active_module_id
            in selected_ids
        ):

            self.detail_panel.refresh_logs()


# ============================================================
# Launcher
# ============================================================

def main():

    app = QApplication(
        sys.argv
    )

    game_result = (
        launch_game()
    )

    print()

    print(
        "MECCHA CHAMELEON TOOLS"
    )

    print(
        "=" * 60
    )

    print(
        f"Game status : "
        f"{game_result['status']}"
    )

    print(
        f"Message     : "
        f"{game_result['message']}"
    )

    print()

    print(
        "Test backend enabled."
    )

    window = (
        MecchaToolsWindow()
    )

    window.show()

    sys.exit(
        app.exec_()
    )


if __name__ == "__main__":

    main()