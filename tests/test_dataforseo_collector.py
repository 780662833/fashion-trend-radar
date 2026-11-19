from __future__ import annotations


from fashion_radar.collectors.dataforseo_trends import (
    fetch_trend_task_results,
    load_query_plan,
    submit_trend_tasks,
)
from fashion_radar.db import connect, initialize


PRODUCT_ID = "PILOT-KOBE-9"


def _database(path):
    initialize(path)
    with connect(path) as conn:
        conn.execute(
            """INSERT INTO products(product_id,category,subcategory,brand,model_name,canonical_name)
               VALUES(?, 'sneaker','basketball','Nike','Kobe 9 EM Low Protro',
                      'Kobe 9 EM Low Protro Philippines')""",
            (PRODUCT_ID,),
        )
    return path


def _query_file(path, rows=None):
    rows = rows or [
        ("exact_phrase", "Kobe 9 EM Low Protro Philippines"),
        ("related_terms", "Kobe 9 Philippines"),
        ("related_terms", "Kobe 9 Protro Philippines"),
    ]
    lines = [
        "product_id,market_code,comparison_set,keyword,date_from,date_to,search_type,language_code"
    ]
    for comparison_set, keyword in rows:
        lines.append(
            f"{PRODUCT_ID},US,{comparison_set},{keyword},2026-08-19,2026-11-19,web,en"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_query_plan_keeps_standalone_and_comparison_sets_separate(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(tmp_path / "queries.csv")
    groups = load_query_plan(query_file, db)

    assert len(groups) == 2
    assert groups[0].comparison_set == "exact_phrase"
    assert groups[0].keywords == ("Kobe 9 EM Low Protro Philippines",)
    assert groups[1].comparison_set == "related_terms"
    assert len(groups[1].keywords) == 2
    assert groups[0].comparison_group != groups[1].comparison_group


class FakeDataForSEOClient:
    def __init__(self):
        self.post_payloads = []
        self.get_endpoints = []

    def post(self, endpoint, payload):
        self.post_payloads.append((endpoint, payload))
        return {
            "status_code": 20000,
            "status_message": "Ok.",
            "tasks": [{
                "id": "task-kobe-9",
                "status_code": 20100,
                "status_message": "Task Created.",
                "cost": 0.0027,
            }],
        }

    def get(self, endpoint):
        self.get_endpoints.append(endpoint)
        keywords = ["Kobe 9 EM Low Protro Philippines", "Kobe 9 Philippines"]
        return {
            "status_code": 20000,
            "tasks": [{
                "id": "task-kobe-9",
                "status_code": 20000,
                "cost": 0,
                "result": [{
                    "keywords": keywords,
                    "check_url": "https://trends.google.com/trends/explore?geo=US",
                    "items": [{
                        "type": "google_trends_graph",
                        "keywords": keywords,
                        "data": [
                            {
                                "date_from": "2026-11-17",
                                "date_to": "2026-11-17",
                                "missing_data": False,
                                "values": [0, 70],
                            },
                            {
                                "date_from": "2026-11-18",
                                "date_to": "2026-11-18",
                                "missing_data": False,
                                "values": [100, 85],
                            },
                            {
                                "date_from": "2026-11-19",
                                "date_to": "2026-11-19",
                                "missing_data": True,
                                "values": [None, None],
                            },
                        ],
                    }],
                }],
            }],
        }


def test_dataforseo_standard_task_is_saved_and_normalized(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(
        tmp_path / "queries.csv",
        [
            ("launch_window", "Kobe 9 EM Low Protro Philippines"),
            ("launch_window", "Kobe 9 Philippines"),
        ],
    )
    client = FakeDataForSEOClient()

    submitted = submit_trend_tasks(db, query_file, client=client, max_tasks=5)
    assert submitted["status"] == "pending_results"
    assert len(submitted["submitted_tasks"]) == 1
    assert client.post_payloads[0][0].endswith("/google_trends/explore/task_post")
    assert client.post_payloads[0][1][0]["location_name"] == "United States"
    assert client.post_payloads[0][1][0]["keywords"] == [
        "Kobe 9 EM Low Protro Philippines",
        "Kobe 9 Philippines",
    ]

    fetched = fetch_trend_task_results(db, run_id=submitted["run_id"], client=client)
    assert fetched["status"] == "success"
    assert fetched["completed_tasks"] == 1
    assert fetched["observations_saved"] == 4
    assert fetched["missing_points"] == 2
    assert client.get_endpoints == [
        "/keywords_data/google_trends/explore/task_get/task-kobe-9"
    ]

    with connect(db) as conn:
        observations = conn.execute(
            """SELECT keyword,metric_value,comparison_group,source_url,notes,is_synthetic,
                      validation_status
               FROM source_observations
               ORDER BY keyword,period_start"""
        ).fetchall()
        snapshots = conn.execute(
            "SELECT COUNT(*) FROM search_snapshots"
        ).fetchone()[0]
        run = conn.execute(
            "SELECT status,items_saved FROM source_runs WHERE run_id=?",
            (submitted["run_id"],),
        ).fetchone()
        task = conn.execute(
            "SELECT status,result_points,missing_points FROM dataforseo_tasks"
        ).fetchone()

    assert len(observations) == 4
    first_keyword = [row for row in observations if row["keyword"] == "Kobe 9 EM Low Protro Philippines"]
    assert [row["metric_value"] for row in first_keyword] == [0, 100]
    assert "possible_below_reporting_threshold=true" in first_keyword[0]["notes"]
    assert all(row["source_url"].startswith("https://trends.google.com/") for row in observations)
    assert all(row["is_synthetic"] == 0 for row in observations)
    assert all(row["validation_status"] == "accepted" for row in observations)
    assert snapshots == 4
    assert run["status"] == "success"
    assert run["items_saved"] == 4
    assert task["status"] == "completed"
    assert task["result_points"] == 4
    assert task["missing_points"] == 2


def test_provider_task_budget_blocks_before_api_call(tmp_path):
    db = _database(tmp_path / "registry.sqlite")
    query_file = _query_file(
        tmp_path / "queries.csv",
        [
            ("one", "Kobe 9 EM Low Protro Philippines"),
            ("two", "Kobe 9 Philippines"),
        ],
    )
    client = FakeDataForSEOClient()

    try:
        submit_trend_tasks(db, query_file, client=client, max_tasks=1)
    except ValueError as exc:
        assert "exceeding the daily safety budget" in str(exc)
    else:
        raise AssertionError("Expected budget guard to reject the task plan")
    assert client.post_payloads == []
