package co.soclose.sosforge

import co.soclose.sosforge.core.alert.AlertContext
import co.soclose.sosforge.core.alert.AlertPrefs
import co.soclose.sosforge.core.alert.AlertRules
import co.soclose.sosforge.core.alert.Effect
import co.soclose.sosforge.core.alert.Ledger
import co.soclose.sosforge.core.alert.Level
import co.soclose.sosforge.core.feed.FeedChange
import co.soclose.sosforge.core.feed.FeedReducer
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.geo.Geo
import co.soclose.sosforge.core.geo.Place
import co.soclose.sosforge.core.live.Protocol
import co.soclose.sosforge.core.live.ServerMessage
import co.soclose.sosforge.core.net.ApiVersion
import co.soclose.sosforge.core.net.EventsReply
import co.soclose.sosforge.core.net.GuidanceReader
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.Scope
import co.soclose.sosforge.core.net.SosJson
import co.soclose.sosforge.core.net.Unplaced
import co.soclose.sosforge.LiveUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The merged v2 contract, on VERBATIM captures of the integration API (see Fixtures). */
class ContractV2Test {
    private val athens = Place("ath", "Athens", 37.98, 23.73, 300, countryCode = "GR")
    private val la = Place("la", "Los Angeles", 34.05, -118.24, 300, countryCode = "US")
    private fun reply(name: String) = SosJson.decodeFromString(EventsReply.serializer(), Fixtures.text(name))

    @Test fun version_names_the_merged_features() {
        val v = SosJson.decodeFromString(ApiVersion.serializer(), Fixtures.text("v2-version.json"))
        assertTrue(v.wsScope)
        assertTrue(v.incremental)
        assertTrue(v.has(ApiVersion.GUIDANCE))
        assertTrue(v.has(ApiVersion.ESCALATED))
        assertTrue(v.has(ApiVersion.SNAPSHOT_PINNED))
    }

    @Test fun a_full_zoned_read_mints_a_cursor_and_says_why_it_reset() {
        val r = reply("v2-events-full.json")
        assertEquals("32b77f83:983", r.cursor)
        assertTrue(r.reset)
        assertEquals("no_cursor", r.resetReason)
        assertEquals(Unplaced(240, 22), Unplaced.of(r.unplaced))
        assertEquals(127, r.events.size)
    }

    @Test fun an_empty_delta_returns_our_own_cursor() {
        val full = reply("v2-events-full.json")
        val delta = reply("v2-events-delta.json")
        assertTrue(delta.events.isEmpty())
        assertTrue(delta.removed.isEmpty())
        assertFalse(delta.reset)
        assertEquals(full.cursor, delta.cursor)
        assertNull(Unplaced.of(delta.unplaced))
    }

    @Test fun the_zoned_snapshot_holds_only_the_zones_and_the_asked_country() {
        val lines = Fixtures.text("v2-ws-zoned.ndjson").lines().filter { it.isNotBlank() }
        val msgs = lines.map { Protocol.parse(it)!! }
        val snap = msgs.first() as ServerMessage.Snapshot
        assertEquals(Unplaced(240, 22), snap.unplaced)
        assertTrue(msgs.drop(1).all { it is ServerMessage.Tick })
        for (e in snap.events) {
            if (e.hasPosition) {
                val d = minOf(Geo.distanceKm(athens, e)!!, Geo.distanceKm(la, e)!!)
                assertTrue("${e.id} at $d km", d <= 300.5)
            } else {
                assertEquals("GR", e.countryCode)
            }
        }
    }

    @Test fun a_refused_scope_is_an_error_message_then_1008() {
        val lines = Fixtures.text("v2-ws-error.ndjson").lines().filter { it.isNotBlank() }
        val m = Protocol.parse(lines.first())
        assertEquals(ServerMessage.Refused("bad_zones", "latitude out of range: 95"), m)
        assertTrue("the capture's own close-frame line", lines.last().contains("1008"))
    }

    @Test fun guidance_over_http_has_every_kind_and_phase_with_an_official_link() {
        for (lang in listOf("en", "fr")) {
            val g = GuidanceReader.read(SosJson.parseToJsonElement(Fixtures.text("guidance-$lang.json")), lang)
            assertEquals(lang, g.lang)
            Kind.entries.forEach { k ->
                val kg = g.of(k.key) ?: throw AssertionError("$lang has no ${k.key}")
                assertTrue("$lang ${k.key}", kg.before.isNotEmpty() && kg.during.isNotEmpty() && kg.after.isNotEmpty())
                (kg.before + kg.during + kg.after).forEach { l ->
                    assertTrue(l.url!!.startsWith("https://"))
                    assertFalse(l.source.isNullOrBlank())
                }
            }
        }
    }

    @Test fun guidance_is_read_from_the_other_shapes_a_catalogue_can_take() {
        // hand-built: the tolerance of the reader, not the server's shape (that is the test above)
        val asList = GuidanceReader.read(
            SosJson.parseToJsonElement("""{"kinds":[{"kind":"tsunami","phases":{"during":[{"line":"Go to high ground.","source_url":"https://www.jma.go.jp/"}]}}]}"""),
            "ja",
        )
        assertEquals("https://www.jma.go.jp/", asList.of("tsunami")!!.during.single().url)
        val js = GuidanceReader.read(SosJson.parseToJsonElement("""{"kinds":{"flood":{"after":[{"text":"Boil water.","url":"javascript:alert(1)"}]}}}"""), "en")
        assertNull(js.of("flood")!!.after.single().url)
    }

    // ------------------------------------------------------------------ which socket

    private val v2 = SosJson.decodeFromString(ApiVersion.serializer(), Fixtures.text("v2-version.json"))

    @Test fun a_zoned_server_is_always_reached_with_zones_never_plain() {
        val o = "https://sosforge.soclose.co"
        val fg = LiveUrl.url(o, v2, listOf(athens), AlertPrefs(), foreground = true, refused = false)!!
        assertTrue(fg.contains("zones="))
        assertFalse("the foreground shows the whole zone", fg.contains("min_severity"))
        assertTrue(fg.contains("unplaced=GR"))
        val bg = LiveUrl.url(o, v2, listOf(athens), AlertPrefs(), foreground = false, refused = false)!!
        assertTrue(bg.contains("min_severity=moderate"))
        assertNull("no place: no socket (an unfiltered snapshot is ~880 KiB)", LiveUrl.url(o, v2, emptyList(), AlertPrefs(), true, false))
        assertNull("contract not known yet: not now", LiveUrl.url(o, null, listOf(athens), AlertPrefs(), true, false))
        assertEquals("wss://sosforge.soclose.co/ws", LiveUrl.url(o, ApiVersion.NONE, listOf(athens), AlertPrefs(), true, false))
        val after = LiveUrl.url(o, v2, listOf(athens), AlertPrefs(), foreground = false, refused = true)!!
        assertTrue(after.contains("zones=") && !after.contains("kinds=") && !after.contains("unplaced="))
    }

    @Test fun the_chunked_snapshot_is_asked_for_only_when_the_server_offers_it() {
        val o = "https://sosforge.soclose.co"
        assertFalse("v2 without snapshot_chunked", LiveUrl.url(o, v2, listOf(athens), AlertPrefs(), true, false)!!.contains("chunks="))
        val chunked = v2.copy(features = v2.features + ApiVersion.SNAPSHOT_CHUNKED)
        assertTrue(LiveUrl.url(o, chunked, listOf(athens), AlertPrefs(), true, false)!!.endsWith("&chunks=1"))
        assertFalse("never after a refusal", LiveUrl.url(o, chunked, listOf(athens), AlertPrefs(), true, true)!!.contains("chunks="))
        assertEquals("never on the legacy path", "wss://sosforge.soclose.co/ws", LiveUrl.url(o, ApiVersion.NONE, listOf(athens), AlertPrefs(), true, false))
    }

    @Test fun unplaced_lists_the_countries_of_places_that_want_them_at_most_ten() {
        val s = Scope.of(listOf(athens, la, athens.copy(id = "x", countryWide = false, countryCode = "FR"), athens.copy(id = "y", countryCode = null)), AlertPrefs())!!
        assertEquals(listOf("GR", "US"), s.unplaced)
        assertTrue(Scope.of(listOf(athens.copy(countryCode = null)), AlertPrefs())!!.unplaced.isEmpty())
    }

    // ------------------------------------------------------------------ positionless warnings by country

    private val now = Fixtures.snapshotTime

    @Test fun a_positionless_warning_rings_by_country_only_at_severe_and_above() {
        val greek = Fixtures.snapshot.events.first { !it.hasPosition && it.countryCode == "GR" }
        val prefs = AlertPrefs(kinds = Kind.entries.map { it.key }.toSet())
        assertNull("moderate stays in the feed", AlertRules.match(greek.copy(severity = "moderate"), listOf(athens), prefs))
        val m = AlertRules.match(greek.copy(severity = "severe"), listOf(athens), prefs)!!
        assertTrue(m.byCountry)
        assertEquals(Level.SEVERE, m.level)
        assertNull("the owner switched it off for this place", AlertRules.match(greek.copy(severity = "severe"), listOf(athens.copy(countryWide = false)), prefs))
        assertNull("another country", AlertRules.match(greek.copy(severity = "extreme"), listOf(la), prefs))
        assertNull("a place without a known country", AlertRules.match(greek.copy(severity = "extreme"), listOf(athens.copy(countryCode = null)), prefs))
    }

    @Test fun a_positionless_warning_first_seen_moderate_rings_when_it_turns_severe() {
        val greek = Fixtures.snapshot.events.first { !it.hasPosition && it.countryCode == "GR" }.copy(ongoing = true, expires = null)
        val prefs = AlertPrefs(kinds = Kind.entries.map { it.key }.toSet())
        val l = Ledger().also { it.onChanges(emptyList(), AlertContext(listOf(athens), prefs, now, now, false), fullRead = true) }
        val ctx = AlertContext(listOf(athens), prefs, now, now, false)
        assertTrue(l.onChanges(listOf(FeedChange.Upserted(greek.copy(severity = "moderate"), null, false, false)), ctx, false).isEmpty())
        val fx = l.onChanges(listOf(FeedChange.Upserted(greek.copy(severity = "severe", revision = greek.revision + 1), null, false, false, escalated = true)), ctx, false)
        assertTrue((fx.single() as Effect.Show).match.byCountry)
    }

    /** Lesson 24: what the country rule costs on the real zoned capture (printed, and bounded). */
    @Test fun replay_the_zoned_capture_with_the_country_rule() {
        val lines = Fixtures.text("v2-ws-zoned.ndjson").lines().filter { it.isNotBlank() }
        val prefs = AlertPrefs()
        val l = Ledger().also { it.onChanges(emptyList(), AlertContext(listOf(athens, la), prefs, now, now, false), fullRead = true) }
        var feed = FeedState()
        var rings = 0
        var byCountry = 0
        for (m in lines.map { Protocol.parse(it)!! }) {
            val t = (m as? ServerMessage.Snapshot)?.serverTimeMs ?: (m as? ServerMessage.Tick)?.serverTimeMs ?: now
            val r = FeedReducer.reduce(feed, m, t)
            feed = r.state
            val fx = l.onChanges(r.changes, AlertContext(listOf(athens, la), prefs, feed.serverNow(t), t, false), fullRead = m is ServerMessage.Snapshot)
            rings += fx.count { it is Effect.Show && it.alert }
            byCountry += fx.count { it is Effect.Show && it.match.byCountry }
        }
        println("zoned replay Athens(GR)+LA(US), as a reconnect: rings=$rings of which by country=$byCountry")
        assertTrue(rings <= 10)
    }
}
