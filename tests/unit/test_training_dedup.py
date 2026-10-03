"""
Tests for near-duplicate clustering of listing descriptions.

What matters downstream is that re-posts of one ad share a cluster (so
they can't straddle the train/test split) while distinct ads that merely
share agency boilerplate do not.
"""

from training.dedup import cluster_near_duplicates, content_hash

BASE = (
    "Предлагаме просторен двустаен апартамент до Южен парк и супермаркет Лидл, "
    "на 10 минути от МОЛ България. Хол с кухненски бокс, спалня, баня с тоалетна, "
    "тераса и мазе. Отоплението е на ТЕЦ, сградата е с акт 16 от 2010 година."
)


class TestContentHash:
    def test_ignores_case_whitespace_and_punctuation(self):
        assert content_hash("До  Южен ПАРК!") == content_hash("до южен парк")

    def test_differs_on_words(self):
        assert content_hash("до Южен парк") != content_hash("до Северен парк")


class TestClusterNearDuplicates:
    def test_reposted_ad_with_small_edit_shares_cluster(self):
        repost = BASE.replace("2010", "2011") + " Оглед по всяко време."
        assert cluster_near_duplicates([BASE, repost]) == [0, 0]

    def test_different_ads_get_different_clusters(self):
        other = (
            "Тристаен апартамент в Лозенец, близо до НДК и метростанция. Две спални, "
            "дневна, кухня и два балкона. Газово отопление, паркомясто в двора."
        )
        assert cluster_near_duplicates([BASE, other]) == [0, 1]

    def test_clustering_is_transitive(self):
        # Sliding 20-word windows: neighbours share 16 of 20 trigrams (0.8),
        # the two ends only 14 of 22 (~0.64) — linked solely through b.
        words = [f"дума{i}" for i in range(24)]
        a, b, c = (" ".join(words[start : start + 20]) for start in (0, 2, 4))
        assert cluster_near_duplicates([a, c], threshold=0.75) == [0, 1]
        assert cluster_near_duplicates([a, b, c], threshold=0.75) == [0, 0, 0]

    def test_ids_are_dense_in_input_order(self):
        assert cluster_near_duplicates(["едно две три", "четири пет шест", "едно две три"]) == [
            0,
            1,
            0,
        ]

    def test_empty_text_is_its_own_cluster(self):
        assert cluster_near_duplicates(["", ""]) == [0, 1]
