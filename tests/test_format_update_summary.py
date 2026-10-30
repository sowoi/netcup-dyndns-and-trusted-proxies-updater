from src.netcup_dyndns import format_update_summary


def test_format_update_summary_no_records():
    assert format_update_summary([]) == "No DNS records were processed."


def test_format_update_summary_single_record():
    updated_records = [
        {
            "domain": "example.com",
            "subdomain": "sub",
            "record_type": "A",
            "destination": "1.2.3.4",
        }
    ]

    summary = format_update_summary(updated_records)

    assert summary == "example.com\n  - sub          A     -> 1.2.3.4"


def test_format_update_summary_groups_multiple_subdomains_by_domain():
    updated_records = [
        {
            "domain": "example.com",
            "subdomain": "sub",
            "record_type": "A",
            "destination": "1.2.3.4",
        },
        {
            "domain": "example.com",
            "subdomain": "sub",
            "record_type": "AAAA",
            "destination": "::1",
        },
        {
            "domain": "example.com",
            "subdomain": "www",
            "record_type": "A",
            "destination": "1.2.3.4",
        },
        {
            "domain": "example.net",
            "subdomain": "app",
            "record_type": "A",
            "destination": "1.2.3.4",
        },
    ]

    summary = format_update_summary(updated_records)
    lines = summary.splitlines()

    # Records are grouped under their domain header, not a flat sequential list.
    assert lines[0] == "example.com"
    assert lines[1].strip().startswith("- sub")
    assert "A" in lines[1] and "1.2.3.4" in lines[1]
    assert lines[2].strip().startswith("- sub")
    assert "AAAA" in lines[2] and "::1" in lines[2]
    assert lines[3].strip().startswith("- www")
    assert lines[4] == "example.net"
    assert lines[5].strip().startswith("- app")


def test_format_update_summary_colors_errors_only_when_enabled():
    records = [
        {
            "domain": "example.com",
            "subdomain": "sub",
            "record_type": "A",
            "destination": "LOGIN FAILED",
            "error": True,
        }
    ]

    plain = format_update_summary(records)
    colored = format_update_summary(records, use_color=True)

    assert "\033" not in plain
    assert plain == "example.com\n  - sub          A     -> LOGIN FAILED"
    assert "\033[91mLOGIN FAILED\033[0m" in colored
