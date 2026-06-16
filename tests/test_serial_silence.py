from altruist_tester.rules.silence import check_serial_silence


def test_check_serial_silence_passes_for_regular_output():
    report = check_serial_silence(
        lines_read=3,
        duration_seconds=300,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=299.0,
        max_interline_gap_seconds=90.0,
    )

    assert report.status == "ok"
    assert report.ok is True
    assert report.max_silence_seconds == 90.0
    assert report.findings == ()


def test_check_serial_silence_warns_for_pause_between_lines():
    report = check_serial_silence(
        lines_read=2,
        duration_seconds=200,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=151.0,
        max_interline_gap_seconds=150.0,
    )

    assert report.status == "warn"
    assert report.ok is True
    assert report.warning_count == 1
    assert report.findings[0].code == "INTERLINE_SERIAL_SILENCE"
    assert report.findings[0].silence_seconds == 150.0


def test_check_serial_silence_fails_without_serial_output():
    report = check_serial_silence(
        lines_read=0,
        duration_seconds=10 * 60,
        first_line_elapsed_seconds=None,
        last_line_elapsed_seconds=None,
        max_interline_gap_seconds=None,
    )

    assert report.status == "fail"
    assert report.ok is False
    assert report.failure_count == 1
    assert report.findings[0].code == "NO_SERIAL_OUTPUT"
    assert "Wi-Fi AP/config portal mode" in report.findings[0].message


def test_check_serial_silence_fails_for_long_tail_silence():
    report = check_serial_silence(
        lines_read=1,
        duration_seconds=20 * 60,
        first_line_elapsed_seconds=1.0,
        last_line_elapsed_seconds=1.0,
        max_interline_gap_seconds=None,
    )

    assert report.status == "fail"
    assert report.failure_count == 1
    assert report.findings[0].code == "TAIL_SERIAL_SILENCE"
    assert report.tail_silence_seconds == 1199.0
