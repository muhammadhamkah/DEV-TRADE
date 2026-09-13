from survival.news import search_news

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Fed holds rates steady - Reuters</title><pubDate>Mon, 14 Sep 2026 10:00:00 GMT</pubDate>
<description>&lt;a href="x"&gt;Fed holds&lt;/a&gt;&amp;nbsp;rates &lt;b&gt;steady&lt;/b&gt; amid inflation worries</description>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Second story</title><pubDate>Sun, 13 Sep 2026 10:00:00 GMT</pubDate><description>b</description></item>
<item><title>Third</title></item>
</channel></rss>"""


class FakeResp:
    content = RSS

    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params))
        return FakeResp()


def test_search_news_parses_rss_and_strips_html():
    s = FakeSession()
    items = search_news("fed rates", days=5, limit=2, session=s)
    assert s.calls[0][1]["q"] == "fed rates when:5d"
    assert len(items) == 2
    assert items[0] == {
        "title": "Fed holds rates steady - Reuters",
        "source": "Reuters",
        "published": "Mon, 14 Sep 2026 10:00:00 GMT",
        "snippet": "Fed holds rates steady amid inflation worries",
    }
    assert items[1]["source"] == ""
