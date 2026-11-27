"""Unit tests for unrestricted & safe website navigation and site search."""

import pytest
from jarvis.safety import RiskLevel
from jarvis.tools.system_tools import open_website, open_site_search, _launch_browser
from jarvis.config import config


def test_open_website_valid_https(mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)
    res = open_website("https://news.ycombinator.com")
    assert "news.ycombinator.com" in res
    mock_browser.assert_called_once_with("https://news.ycombinator.com")


def test_open_website_favorite_shortcut(mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)
    res_netflix = open_website("netflix")
    assert "netflix.com" in res_netflix
    mock_browser.assert_called_with("https://www.netflix.com")

    res_spotify = open_website("spotify")
    assert "spotify.com" in res_spotify
    mock_browser.assert_called_with("https://open.spotify.com")


def test_open_website_unknown_plain_name_fallback_to_search(mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)
    res = open_website("quantum computing breakthroughs")
    assert "quantum computing breakthroughs" in res
    assert "google.com/search" in res
    mock_browser.assert_called_once_with("https://www.google.com/search?q=quantum+computing+breakthroughs")


@pytest.mark.parametrize("bad_url", [
    "file:///C:/Windows/System32/calc.exe",
    "javascript:alert(1)",
    "data:text/html,<h1>Pwned</h1>",
    "vbscript:msgbox",
    "ftp://ftp.example.com/file.txt",
])
def test_open_website_prohibited_schemes(bad_url, mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser")
    res = open_website(bad_url)
    assert "access blocked" in res.lower()
    assert "prohibited" in res.lower()
    mock_browser.assert_not_called()


def test_open_website_http_confirmation_denied(mocker):
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=False)
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser")

    res = open_website("http://insecure.internal.lan")
    assert "cancelled" in res.lower()
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args[0][0] == RiskLevel.MEDIUM
    mock_browser.assert_not_called()


def test_open_website_http_confirmation_approved(mocker):
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=True)
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)

    res = open_website("http://insecure.internal.lan")
    assert "insecure.internal.lan" in res
    mock_confirm.assert_called_once()
    mock_browser.assert_called_once_with("http://insecure.internal.lan")


def test_open_website_raw_ip_confirmation_denied(mocker):
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=False)
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser")

    res = open_website("https://192.168.1.1/admin")
    assert "cancelled" in res.lower()
    mock_confirm.assert_called_once()
    assert mock_confirm.call_args[0][0] == RiskLevel.MEDIUM
    assert "IP address" in mock_confirm.call_args[0][1]
    mock_browser.assert_not_called()


def test_open_website_raw_ip_confirmation_approved(mocker):
    mock_confirm = mocker.patch("jarvis.tools.system_tools.confirm_action", return_value=True)
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)

    res = open_website("https://192.168.1.1/admin")
    assert "192.168.1.1" in res
    mock_confirm.assert_called_once()
    mock_browser.assert_called_once_with("https://192.168.1.1/admin")


def test_open_site_search_templates(mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)

    # Netflix
    res_netflix = open_site_search("netflix", "Stranger Things")
    assert "Stranger Things" in res_netflix
    mock_browser.assert_called_with("https://www.netflix.com/search?q=Stranger+Things")

    # YouTube
    res_yt = open_site_search("youtube", "Inception soundtrack")
    assert "Inception soundtrack" in res_yt
    mock_browser.assert_called_with("https://www.youtube.com/results?search_query=Inception+soundtrack")

    # Amazon
    res_amz = open_site_search("amazon", "wireless mouse")
    assert "wireless mouse" in res_amz
    mock_browser.assert_called_with("https://www.amazon.com/s?k=wireless+mouse")

    # Spotify
    res_spotify = open_site_search("spotify", "Daft Punk")
    assert "Daft Punk" in res_spotify
    mock_browser.assert_called_with("https://open.spotify.com/search/Daft+Punk")

    # GitHub
    res_gh = open_site_search("github", "fastapi")
    assert "fastapi" in res_gh
    mock_browser.assert_called_with("https://github.com/search?q=fastapi")

    # Wikipedia
    res_wiki = open_site_search("wikipedia", "Alan Turing")
    assert "Alan Turing" in res_wiki
    mock_browser.assert_called_with("https://en.wikipedia.org/wiki/Special:Search?search=Alan+Turing")


def test_open_site_search_unknown_site_fallback(mocker):
    mock_browser = mocker.patch("jarvis.tools.system_tools._launch_browser", return_value=True)
    res = open_site_search("coursera", "machine learning")
    assert "machine learning" in res
    mock_browser.assert_called_with("https://www.google.com/search?q=site:coursera+machine+learning")


def test_launch_browser_chrome(mocker):
    mock_popen = mocker.patch("subprocess.Popen")
    mocker.patch("os.path.exists", return_value=True)
    
    # Temporarily set browser to chrome
    orig = config.browser
    try:
        config.browser = "chrome"
        success = _launch_browser("https://example.com")
        assert success is True
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert "chrome.exe" in args[0].lower()
        assert args[1] == "https://example.com"
    finally:
        config.browser = orig


def test_launch_browser_default(mocker):
    mock_open = mocker.patch("webbrowser.open", return_value=True)
    orig = config.browser
    try:
        config.browser = "default"
        success = _launch_browser("https://example.com")
        assert success is True
        mock_open.assert_called_once_with("https://example.com")
    finally:
        config.browser = orig
