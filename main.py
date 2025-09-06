from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QPushButton, QFrame
from PyQt5.QtCore import Qt, QThread, pyqtSignal
import subprocess
import sys
import json
import time


class StatusChecker(QThread):
    """Run `tailscale status` in a background thread and emit the result."""

    # emit: connected, tooltip (raw/pretty output), hostname, user/tailnet name, exit_node_host
    statusChanged = pyqtSignal(bool, str, str, str, str)

    def __init__(self):
        super().__init__()
        # Control flag to allow clean shutdown and repeated checks
        self._running = True

    def stop(self):
        # Signal the run loop to exit
        self._running = False

    def run(self):
        # Run the check once now and then repeat every 5 seconds until stopped
        while self._running:
            try:
                # Use JSON output for reliable parsing and only query local machine
                proc = subprocess.run([
                    "tailscale",
                    "status",
                    "--json",
                    "--self",
                ], capture_output=True, text=True, timeout=6)

                out = (proc.stdout or "").strip()
                err = (proc.stderr or "").strip()

                connected = False
                tooltip = out or err or "(no output)"
                hostname = ""
                user = ""
                exit_node_host = "NO"

                if proc.returncode == 0 and out:
                    try:
                        data = json.loads(out)
                        backend_state = data.get("BackendState")
                        # BackendState is "Running" when active; other values (NeedsLogin, Stopped, etc.) mean not running
                        if backend_state == "Running":
                            connected = True
                        else:
                            connected = False
                        # Pretty-print JSON for tooltip/debug
                        tooltip = json.dumps(data, indent=2)

                        # Extract requested fields (use safe lookups)
                        self_info = data.get("Self") or {}
                        hostname = self_info.get("HostName") or ""
                        current_tailnet = data.get("CurrentTailnet") or {}
                        user = current_tailnet.get("Name") or ""

                        # Determine exit node: default NO. If ExitNodeStatus exists, find peer with matching ID
                        exit_status = data.get("ExitNodeStatus") or {}
                        exit_id = exit_status.get("ID")
                        if exit_id:
                            # Peer list may be under 'Peer' or 'Peers' depending on version; handle both
                            peers = data.get("Peer") or data.get("Peers") or {}
                            # peers is expected to be a dict of {randomName: {...}}
                            if isinstance(peers, dict):
                                for p in peers.values():
                                    try:
                                        if p.get("ID") == exit_id:
                                            exit_node_host = p.get("HostName") or exit_id
                                            break
                                    except Exception:
                                        continue
                    except Exception:
                        # If JSON parsing fails, fall back to using raw output
                        connected = False
                        tooltip = out or err
                        hostname = ""
                        user = ""
                        exit_node_host = "NO"
                else:
                    # Non-zero return or empty output -> treat as disconnected and show stderr/stdout
                    connected = False
                    tooltip = err or out
                    hostname = ""
                    user = ""
                    exit_node_host = "NO"

                # Emit connected state, tooltip, hostname, user and exit node host
                self.statusChanged.emit(connected, tooltip, hostname, user, exit_node_host)
            except Exception as e:
                self.statusChanged.emit(False, str(e), "", "", "NO")

            # Wait up to 10 seconds but remain responsive to stop requests by
            # sleeping in short increments and checking the _running flag.
            for _ in range(50):
                if not self._running:
                    break
                time.sleep(0.1)


class CommandRunner(QThread):
    """Run a single CLI command in a background thread and emit the result."""

    finished = pyqtSignal(bool, str)

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd

    def run(self):
        try:
            proc = subprocess.run(self.cmd, capture_output=True, text=True, timeout=30)
            out = (proc.stdout or "").strip()
            err = (proc.stderr or "").strip()
            success = proc.returncode == 0
            output = out or err or "(no output)"
            self.finished.emit(success, output)
        except Exception as e:
            self.finished.emit(False, str(e))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("My Tailscale")
        self.setFixedSize(360, 220)

        self.status_label = QLabel("Checking...", alignment=Qt.AlignCenter)
        self.status_label.setFixedHeight(48)
        self.status_label.setStyleSheet("font-weight: bold; padding: 8px; border-radius: 6px;")

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)

        # Horizontal separator
        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.HLine)
        self.sep.setFrameShadow(QFrame.Sunken)
        layout.addWidget(self.sep)

        # Action button that reflects connection state
        self.action_button = QPushButton("...", clicked=self.on_action_clicked)
        self.action_button.setFixedHeight(36)
        self.action_button.setStyleSheet("font-weight: bold; border-radius: 6px;")
        layout.addWidget(self.action_button)

        # Separator between button and small status table
        self.sep2 = QFrame()
        self.sep2.setFrameShape(QFrame.HLine)
        self.sep2.setFrameShadow(QFrame.Sunken)
        layout.addWidget(self.sep2)

        # Small status table: two labels (Hostname and User)
        self.hostname_label = QLabel("Hostname : (checking...)", alignment=Qt.AlignLeft)
        self.hostname_label.setStyleSheet("padding: 4px;")
        layout.addWidget(self.hostname_label)

        self.user_label = QLabel("User : (checking...)", alignment=Qt.AlignLeft)
        self.user_label.setStyleSheet("padding: 4px;")
        layout.addWidget(self.user_label)

        # Exit node label
        self.exit_node_label = QLabel("Exit Node : (checking...)", alignment=Qt.AlignLeft)
        self.exit_node_label.setStyleSheet("padding: 4px;")
        layout.addWidget(self.exit_node_label)

        layout.addStretch()
        self.setLayout(layout)

        # Start background checker
        self.checker = StatusChecker()
        self.checker.statusChanged.connect(self.update_status)
        self.checker.start()

    def update_status(self, connected: bool, output: str, hostname: str, user: str, exit_node_host: str):
        # keep latest state
        self.connected = connected
        if connected:
            # green background with white text
            self.status_label.setText("Connected")
            self.status_label.setStyleSheet(
                "color: white; background-color: #333333; font-weight: bold; padding: 8px; border-radius: 6px;"
            )
            # update button
            self.action_button.setText("Click to disconnect")
            self.action_button.setStyleSheet(
                "color: white; background-color: #2ecc71; font-weight: bold; padding: 8px; border-radius: 6px;"
            )
        else:
            self.status_label.setText("Disconnected")
            self.status_label.setStyleSheet(
                "color: white; background-color: #333333; font-weight: bold; padding: 8px; border-radius: 6px;"
            )
            self.action_button.setText("Click to connect")
            self.action_button.setStyleSheet(
                "color: white; background-color: #e74c3c; font-weight: bold; padding: 8px; border-radius: 6px;"
            )

        # Update the small status table
        try:
            self.hostname_label.setText(f"Hostname : {hostname or '(unknown)'}")
            self.user_label.setText(f"User : {user or '(unknown)'}")
            self.exit_node_label.setText(f"Exit Node : {exit_node_host or '(unknown)'}")
        except Exception:
            pass

        # Show raw output on hover for debugging
        self.status_label.setToolTip(output or "(no output)")

    def closeEvent(self, event):
        # Stop the background thread cleanly before closing the window
        try:
            if hasattr(self, 'checker') and self.checker.isRunning():
                self.checker.stop()
                self.checker.wait()
            # if a command runner is active, wait for it briefly
            if hasattr(self, 'runner') and isinstance(self.runner, QThread) and self.runner.isRunning():
                try:
                    self.runner.wait(1000)
                except Exception:
                    pass
        except Exception:
            pass
        super().closeEvent(event)

    def on_action_clicked(self):
        """Run connect/disconnect command based on current state."""
        # Disable button while running
        try:
            self.action_button.setEnabled(False)
        except Exception:
            pass

        if getattr(self, 'connected', False):
            cmd = ["tailscale", "down"]
        else:
            cmd = ["tailscale", "up"]

        self.runner = CommandRunner(cmd)
        self.runner.finished.connect(self._on_command_finished)
        self.runner.start()

    def _on_command_finished(self, success: bool, output: str):
        # Re-enable the button and show command output as tooltip. The StatusChecker
        # will detect state changes on its next poll.
        try:
            self.action_button.setEnabled(True)
            self.action_button.setToolTip(output or "(no output)")
        except Exception:
            pass


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
