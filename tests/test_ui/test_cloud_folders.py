"""Cloud-Sync-Ordner erkennen (reine Pfadprüfung, kein Dateizugriff)."""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

import pytest

from insurance_app.ui.cloud_folders import cloud_sync_provider, cloud_sync_warning


@pytest.mark.parametrize(
    ("path", "provider"),
    [
        # macOS
        ("/Users/muster/Library/Mobile Documents/com~apple~CloudDocs/Exporte", "iCloud Drive"),
        ("/Users/muster/Library/Mobile Documents/Exporte", "iCloud Drive"),
        ("/Users/muster/iCloud Drive/Exporte", "iCloud Drive"),
        ("/Users/muster/Library/CloudStorage/OneDrive-MusterGmbH/Exporte", "OneDrive"),
        (
            "/Users/muster/Library/CloudStorage/GoogleDrive-muster@example.invalid/My Drive/PDF",
            "Google Drive",
        ),
        ("/Users/muster/Library/CloudStorage/Dropbox/PDF", "Dropbox"),
        ("/Users/muster/Library/CloudStorage/Box-Box/PDF", "Box"),
        ("/Users/muster/Dropbox/Exporte", "Dropbox"),
        ("/Users/muster/Dropbox (Muster GmbH)/Exporte", "Dropbox"),
        ("/Users/muster/Google Drive/Exporte", "Google Drive"),
        ("/Volumes/GoogleDrive/My Drive/Exporte", "Google Drive"),
        ("/Users/muster/Nextcloud/Exporte", "Nextcloud"),
        ("/Users/muster/pCloud Drive/Exporte", "pCloud"),
        ("/Users/muster/Box/Exporte", "Box"),
        ("/Users/muster/Box Sync/Exporte", "Box"),
        # Windows
        (r"C:\Users\Muster\OneDrive\Dokumente\Exporte", "OneDrive"),
        (r"C:\Users\Muster\OneDrive - Muster GmbH\Exporte", "OneDrive"),
        (r"c:\users\muster\onedrive\exporte", "OneDrive"),
        (r"C:\Users\Muster\iCloudDrive\Exporte", "iCloud Drive"),
        (r"C:\Users\Muster\DROPBOX\Exporte", "Dropbox"),
        (r"G:\My Drive\Exporte", "Google Drive"),
        (r"G:\Meine Ablage\Exporte", "Google Drive"),
        (r"C:\Users\Muster\GoogleDrive\Exporte", "Google Drive"),
        (r"C:\Users\Muster\Nextcloud\Exporte", "Nextcloud"),
        (r"P:\pCloud Sync\Exporte", "pCloud"),
        (r"C:\Users\Muster\pCloudDrive", "pCloud"),
        (r"C:\Users\Muster\Box\Exporte", "Box"),
        # Linux
        ("/home/muster/Nextcloud2/exporte", "Nextcloud"),
        ("/home/muster/dropbox", "Dropbox"),
    ],
)
def test_cloud_folders_are_detected(path, provider):
    assert cloud_sync_provider(path) == provider
    assert cloud_sync_provider(path.upper()) == provider  # Groß-/Kleinschreibung egal
    text = cloud_sync_warning(path)
    assert text.startswith("⚠") and provider in text  # Symbol + Text
    assert "Die App selbst sendet nichts" in text


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "   ",
        "/Users/muster/Documents/Lokales-Vorlagen-Tool/Exporte",
        "/Users/muster/Library/Application Support/Lokales-Vorlagen-Tool",
        "/Users/muster/Documents/Dropbox-Rechnungen",
        "/Users/muster/Documents/Boxen/Exporte",
        "/home/muster/Dokumente/Mailbox/Exporte",
        "/home/muster/meine-nextcloud-notizen/exporte",
        r"C:\Users\Muster\Documents\Exporte",
        r"C:\Users\Muster\Documents\OneDriveBackup\Exporte",
        r"D:\Daten\Drive\Exporte",
        "/Users/muster/Mobile Documents/Exporte",  # nur unter ~/Library ein iCloud-Ordner
    ],
)
def test_local_folders_give_no_warning(path):
    assert cloud_sync_provider(path) is None
    assert cloud_sync_warning(path) == ""


def test_pure_path_objects_are_accepted():
    assert cloud_sync_provider(PureWindowsPath(r"C:\Users\Muster\OneDrive\Exporte")) == "OneDrive"
    assert cloud_sync_provider(PurePosixPath("/Users/muster/Dropbox/Exporte")) == "Dropbox"
