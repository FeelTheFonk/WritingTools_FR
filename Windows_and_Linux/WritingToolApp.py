import json
import logging
import os
import sys
import threading
import time

import darkdetect
import pyperclip
from pynput import keyboard as pykeyboard
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QMessageBox

from aiprovider import Gemini15FlashProvider, OpenAICompatibleProvider
from ui.AboutWindow import AboutWindow
from ui.CustomPopupWindow import CustomPopupWindow
from ui.OnboardingWindow import OnboardingWindow
from ui.SettingsWindow import SettingsWindow


class WritingToolApp(QtWidgets.QApplication):
    """
    The main application class for Writing Tools.
    """
    output_ready_signal = Signal(str)
    show_message_signal = Signal(str, str)  # New signal for showing message boxes
    hotkey_triggered_signal = Signal()

    def __init__(self, argv):
        super().__init__(argv)
        logging.debug('Initializing WritingToolApp')
        self.output_ready_signal.connect(self.replace_text)
        self.show_message_signal.connect(self.show_message_box)
        self.hotkey_triggered_signal.connect(self.on_hotkey_pressed)
        self.config = None
        self.config_path = None
        self.load_config()
        self.onboarding_window = None
        self.popup_window = None
        self.tray_icon = None
        self.settings_window = None
        self.about_window = None
        self.registered_hotkey = None
        self.output_queue = ""
        self.last_replace = 0
        self.hotkey_listener = None

        # Setup available AI providers
        self.providers = [Gemini15FlashProvider(self), OpenAICompatibleProvider(self)]

        if not self.config:
            logging.debug('No config found, showing onboarding')
            self.show_onboarding()
        else:
            logging.debug('Config found, setting up hotkey and tray icon')

            # Initialize the current provider, defaulting to Gemini 1.5 Flash
            provider_name = self.config.get('provider', 'Gemini 1.5 Flash')

            self.current_provider = next((provider for provider in self.providers if provider.provider_name == provider_name), None)
            if not self.current_provider:
                logging.warning(f'Provider {provider_name} not found. Using default provider.')
                self.current_provider = self.providers[0]

            self.current_provider.load_config(self.config.get("providers", {}).get(provider_name, {}))

            self.create_tray_icon()
            self.register_hotkey()

    def load_config(self):
        """
        Load the configuration file.
        """
        self.config_path = os.path.join(os.path.dirname(sys.argv[0]), 'config.json')
        logging.debug(f'Loading config from {self.config_path}')
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
                logging.debug('Config loaded successfully')
        else:
            logging.debug('Config file not found')
            self.config = None

    def save_config(self, config):
        """
        Save the configuration file.
        """
        with open(self.config_path, 'w') as f:
            json.dump(config, f, indent=4)
            logging.debug('Config saved successfully')
        self.config = config

    def show_onboarding(self):
        """
        Show the onboarding window for first-time users.
        """
        logging.debug('Showing onboarding window')
        self.onboarding_window = OnboardingWindow(self)
        self.onboarding_window.close_signal.connect(self.exit_app)
        self.onboarding_window.show()

    def start_hotkey_listener(self):
        """
        Create listener for hotkeys on Linux/Mac.
        """
        orig_shortcut = self.config.get('shortcut', 'ctrl+space')
        # Parse the shortcut string, for example ctrl+alt+h -> <ctrl>+<alt>+h
        shortcut = '+'.join([f'{t}' if len(t) <= 1 else f'<{t}>' for t in orig_shortcut.split('+')])
        logging.debug(f'Registering global hotkey for shortcut: {shortcut}')
        try:
            if self.hotkey_listener is not None:
                self.hotkey_listener.stop()

            def on_activate():
                logging.debug('triggered hotkey')
                self.hotkey_triggered_signal.emit()  # Emit the signal when hotkey is pressed

            # Define the hotkey combination
            hotkey = pykeyboard.HotKey(
                pykeyboard.HotKey.parse(shortcut),
                on_activate
            )
            self.registered_hotkey = orig_shortcut

            # Helper function to standardize key event
            def for_canonical(f):
                return lambda k: f(self.hotkey_listener.canonical(k))

            # Create a listener and store it as an attribute to stop it later
            self.hotkey_listener = pykeyboard.Listener(
                on_press=for_canonical(hotkey.press),
                on_release=for_canonical(hotkey.release)
            )

            # Start the listener
            self.hotkey_listener.start()
        except Exception as e:
            logging.error(f'Failed to register hotkey: {e}')

    def register_hotkey(self):
        """
        Register the global hotkey for activating Writing Tools.
        """
        logging.debug('Registering hotkey')
        self.start_hotkey_listener()
        logging.debug('Hotkey registered')

    def on_hotkey_pressed(self):
        """
        Handle the hotkey press event.
        """
        logging.debug('Hotkey pressed')

        if self.current_provider:
            logging.debug("Cancelling current provider's request")
            self.current_provider.cancel()
            self.output_queue = ""

        QtCore.QMetaObject.invokeMethod(self, "_show_popup", QtCore.Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _show_popup(self):
        """
        Show the popup window when the hotkey is pressed.
        """
        logging.debug('Showing popup window')
        selected_text = self.get_selected_text()

        logging.debug(f'Selected text: "{selected_text}"')
        try:
            if self.popup_window is not None:
                logging.debug('Existing popup window found')
                if self.popup_window.isVisible():
                    logging.debug('Closing existing visible popup window')
                    self.popup_window.close()
                self.popup_window = None
            logging.debug('Creating new popup window')
            self.popup_window = CustomPopupWindow(self, selected_text)

            # Set the window icon
            icon_path = os.path.join(os.path.dirname(sys.argv[0]), 'icons', 'app_icon.png')
            if os.path.exists(icon_path): self.setWindowIcon(QtGui.QIcon(icon_path))
            # Get the screen containing the cursor
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos)
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            screen_geometry = screen.geometry()
            logging.debug(f'Cursor is on screen: {screen.name()}')
            logging.debug(f'Screen geometry: {screen_geometry}')
            # Show the popup to get its size
            self.popup_window.show()
            self.popup_window.adjustSize()
            # Ensure the popup it's focused, even on lower-end machines
            self.popup_window.activateWindow()
            QtCore.QTimer.singleShot(100, self.popup_window.custom_input.setFocus)

            popup_width = self.popup_window.width()
            popup_height = self.popup_window.height()
            # Calculate position
            x = cursor_pos.x()
            y = cursor_pos.y() + 20  # 20 pixels below cursor
            # Adjust if the popup would go off the right edge of the screen
            if x + popup_width > screen_geometry.right():
                x = screen_geometry.right() - popup_width
            # Adjust if the popup would go off the bottom edge of the screen
            if y + popup_height > screen_geometry.bottom():
                y = cursor_pos.y() - popup_height - 10  # 10 pixels above cursor
            self.popup_window.move(x, y)
            logging.debug(f'Popup window moved to position: ({x}, {y})')
        except Exception as e:
            logging.error(f'Error showing popup window: {e}', exc_info=True)

    def get_selected_text(self):
        """
        Get the currently selected text from any application.
        """
        # Backup the clipboard
        clipboard_backup = pyperclip.paste()
        logging.debug(f'Clipboard backup: "{clipboard_backup}"')

        # Clear the clipboard
        self.clear_clipboard()

        # Simulate Ctrl+C
        logging.debug('Simulating Ctrl+C')

        kbrd = pykeyboard.Controller()

        def press_ctrl_c():
            kbrd.press(pykeyboard.Key.ctrl.value)
            kbrd.press('c')
            kbrd.release('c')
            kbrd.release(pykeyboard.Key.ctrl.value)

        press_ctrl_c()

        # Wait for the clipboard to update
        time.sleep(0.2)

        # Get the selected text
        selected_text = pyperclip.paste()
        logging.debug(f'Selected text: "{selected_text}"')

        # Restore the clipboard
        pyperclip.copy(clipboard_backup)

        return selected_text

    @staticmethod
    def clear_clipboard():
        """
        Clear the system clipboard.
        """
        try:
            pyperclip.copy('')
        except Exception as e:
            logging.error(f'Error clearing clipboard: {e}')

    def process_option(self, option, selected_text, custom_change=None):
        """
        Process the selected writing option in a separate thread.
        """
        logging.debug(f'Processing option: {option}')
        threading.Thread(target=self.process_option_thread, args=(option, selected_text, custom_change), daemon=True).start()

    def process_option_thread(self, option, selected_text, custom_change=None):
        """
        Thread function to process the selected writing option using the AI model.
        """
        logging.debug(f'Starting processing thread for option: {option}')
        try:
            option_prompts = {
                "Relecture": [
                    "Relis et corrige ceci :\n\n",
                    "Tu es un relecteur précis. Corrige les erreurs, fond comme forme, grammaticales, orthographiques et typographiques également. Renvoie uniquement le texte corrigé, en préservant le style et la structure d'origine. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'. Pas de commentaires additionnels."
                ],
                "Réécriture": [
                    "Réécris ceci :\n\n",
                    "Tu es un assistant de rédaction. Améliore la formulation du texte fourni. Renvoie uniquement le texte réécrit, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Amical": [
                    "Rends ceci plus amical :\n\n",
                    "Tu es un assistant de rédaction. Réécris le texte pour le rendre plus chaleureux et accessible. Renvoie uniquement le texte modifié, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Professionnel": [
                    "Rends ceci plus professionnel :\n\n",
                    "Tu es un assistant de rédaction. Réécris le texte dans un style plus formel et professionnel. Renvoie uniquement le texte modifié, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Concis": [
                    "Rends ceci plus concis :\n\n",
                    "Tu es un assistant de rédaction. Réécris le texte de façon plus concise sans perdre l'information essentielle. Renvoie uniquement la version condensée, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Résumé": [
                    "Résume ceci :\n\n",
                    "Tu es un assistant de synthèse. Fournis un résumé clair et concis du texte. Renvoie uniquement le résumé, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Points-Clés": [
                    "Extrais les points clés de ceci :\n\n",
                    "Tu es un assistant d'analyse. Identifie et liste uniquement les points essentiels du texte, sans commentaires. En cas de texte incompréhensible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Tableau": [
                    "Convertis ceci en tableau :\n\n",
                    "Tu es un assistant de conversion. Transforme le texte en tableau structuré. Renvoie uniquement le tableau formaté, sans commentaires. En cas de texte incompatible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ],
                "Personnalisé": [
                    "Applique le changement suivant à ce texte :\n\n",
                    "Tu es un assistant de rédaction polyvalent. Applique précisément la modification demandée au texte fourni. Renvoie uniquement le contenu modifié, sans commentaires. En cas de texte incompatible, renvoie 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'."
                ]
            }

            if selected_text.strip() == '':
                # No selected text
                if option == 'Custom':
                    prompt = custom_change
                    system_instruction = "You are a helpful assistant to the user. The user cannot follow-up with you after your single response to them, so do not ask them questions."
                else:
                    self.show_message_signal.emit('Error', 'Please select text to use this option.')
                    return
            else:
                prompt_prefix, system_instruction = option_prompts.get(option, ('', ''))
                if option == 'Custom':
                    prompt = f"{prompt_prefix}Described change: {custom_change}\n\nText: {selected_text}"
                else:
                    prompt = f"{prompt_prefix}{selected_text}"

            self.output_queue = ""

            self.current_provider.get_response(system_instruction, prompt)

        except Exception as e:
            logging.error(f'An error occurred: {e}', exc_info=True)
            self.show_message_signal.emit('Error', f'An error occurred: {e}')

    @Slot(str, str)
    def show_message_box(self, title, message):
        """
        Show a message box with the given title and message.
        """
        QMessageBox.warning(None, title, message)

    def replace_text(self, new_text):
        """
        Replace the selected text with the new text generated by the AI.
        """
        error_message = 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'

        # Confirm new_text exists and is a string
        if new_text and isinstance(new_text, str):
            self.output_queue += new_text
            current_output = self.output_queue.strip()  # Strip whitespace for comparison

            # If the new text is the error message, show a message box
            if current_output == error_message:
                self.show_message_signal.emit('Error', 'The text is incompatible with the requested change.')
                return

            # Check if we're building up to the error message (to prevent partial pasting)
            # Only do this check if the current output length is less than error message
            if len(current_output) <= len(error_message):
                # Remove all whitespace for comparison to handle any format
                clean_current = ''.join(current_output.split())
                clean_error = ''.join(error_message.split())
                if clean_current == clean_error[:len(clean_current)]:
                    return

            logging.debug('Replacing text')
            try:
                # Backup the clipboard
                clipboard_backup = pyperclip.paste()

                # Clean the output text and set the clipboard
                cleaned_text = self.output_queue.rstrip('\n')  # Remove trailing newlines
                pyperclip.copy(cleaned_text)

                # Simulate Ctrl+V
                logging.debug('Simulating Ctrl+V')

                kbrd = pykeyboard.Controller()

                def press_ctrl_v():
                    kbrd.press(pykeyboard.Key.ctrl.value)
                    kbrd.press('v')
                    kbrd.release('v')
                    kbrd.release(pykeyboard.Key.ctrl.value)

                press_ctrl_v()

                # Wait for the paste operation to complete
                time.sleep(0.2)

                # Restore the clipboard
                pyperclip.copy(clipboard_backup)

                self.output_queue = ""
            except Exception as e:
                logging.error(f'Error replacing text: {e}')
        else:
            logging.debug('No new text to replace')

    def create_tray_icon(self):
        """
        Create the system tray icon for the application.
        """
        if self.tray_icon:
            logging.debug('Tray icon already exists')
            return

        logging.debug('Creating system tray icon')
        icon_path = os.path.join(os.path.dirname(sys.argv[0]), 'icons', 'app_icon.png')
        if not os.path.exists(icon_path):
            logging.warning(f'Tray icon not found at {icon_path}')
            # Use a default icon if not found
            self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        else:
            self.tray_icon = QtWidgets.QSystemTrayIcon(QtGui.QIcon(icon_path), self)
        # Set the tooltip (hover name) for the tray icon
        self.tray_icon.setToolTip("WritingTools")
        tray_menu = QtWidgets.QMenu()

        # Apply dark mode styles using darkdetect
        self.apply_dark_mode_styles(tray_menu)

        settings_action = tray_menu.addAction('Settings')
        settings_action.triggered.connect(self.show_settings)

        about_action = tray_menu.addAction('About')
        about_action.triggered.connect(self.show_about)

        exit_action = tray_menu.addAction('Exit')
        exit_action.triggered.connect(self.exit_app)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()
        logging.debug('Tray icon displayed')

    @staticmethod
    def apply_dark_mode_styles(menu):
        """
        Apply styles to the tray menu based on system theme using darkdetect.
        """
        is_dark_mode = darkdetect.isDark()
        palette = menu.palette()

        if is_dark_mode:
            logging.debug('Tray icon dark')
            # Dark mode colors
            palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#2d2d2d"))  # Dark background
            palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#ffffff"))  # White text
        else:
            logging.debug('Tray icon light')
            # Light mode colors
            palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#ffffff"))  # Light background
            palette.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#000000"))  # Black text

        menu.setPalette(palette)

    def show_settings(self, providers_only=False):
        """
        Show the settings window.
        """
        logging.debug('Showing settings window')
        # Always create a new settings window to handle providers_only correctly
        self.settings_window = SettingsWindow(self, providers_only=providers_only)
        self.settings_window.show()


    def show_about(self):
        """
        Show the about window.
        """
        logging.debug('Showing about window')
        if not self.about_window:
            self.about_window = AboutWindow()
        self.about_window.show()

    def exit_app(self):
        """
        Exit the application.
        """
        logging.debug('Stopping the listener')
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        logging.debug('Exiting application')
        self.quit()
