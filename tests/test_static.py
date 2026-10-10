def test_static_files_are_always_revalidated(client):
    # 캐시 헤더가 없으면 브라우저가 옛 JS 를 재사용해 새 HTML 과 어긋날 수 있다.
    r = client.get("/teacher.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"

    # 바뀌지 않았으면 본문 없이 304 로 끝나므로 매번 확인해도 부담이 없다.
    again = client.get("/teacher.js", headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_html_pages_are_revalidated_too(client):
    for path in ("/", "/teacher.html", "/dashboard.html", "/classes.html", "/classes.js", "/style.css"):
        assert client.get(path).headers["cache-control"] == "no-cache", path
