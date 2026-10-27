package co.soclose.sosforge.feature.common

import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.feed.FeedState
import co.soclose.sosforge.core.live.Link
import co.soclose.sosforge.ui.kit.Banner
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.kit.rememberNow

/** What the screen says about the data it shows (pure, FeedStatusTest). */
enum class DataNote { NONE, CACHE, OFFLINE, UNREACHABLE, BUSY, WAITING }

object FeedStatusRules {
    /** Which honest sentence goes above the data. LIVE data needs none. */
    fun note(link: Link, feed: FeedState): DataNote = when {
        link == Link.LIVE && !feed.fromCache -> DataNote.NONE
        feed.events.isEmpty() && feed.lastMessageAt == 0L && !feed.fromCache && link == Link.CONNECTING -> DataNote.WAITING
        link == Link.OFFLINE -> DataNote.OFFLINE
        link == Link.BUSY -> DataNote.BUSY
        feed.fromCache -> DataNote.CACHE
        link == Link.RECONNECTING -> DataNote.UNREACHABLE
        else -> DataNote.NONE
    }

    /** Device time the shown data was last known good (the last message, else the disk copy). */
    fun dataAt(feed: FeedState): Long? = when {
        feed.fromCache -> feed.cacheSavedAt
        feed.lastMessageAt > 0 -> feed.lastMessageAt
        else -> null
    }
}

/** The link state, re-read every second (the pill and the banners follow the watchdog, not a guess). */
@Composable
fun rememberLink(): Link {
    val now = rememberNow(1_000L)
    val ctx = LocalContext.current
    val sock by ctx.app.live.state.collectAsStateWithLifecycle()
    val net by ctx.app.network.collectAsStateWithLifecycle()
    // keyed on the socket and network states too, so a change recomposes at once (not a second later)
    return remember(sock, net, now) { ctx.app.link(now) }
}

/** "Unreachable, retrying. Last data 3 min old." -- a network error never blames the server. */
@Composable
fun FeedBanner(link: Link, feed: FeedState, modifier: Modifier = Modifier) {
    val note = FeedStatusRules.note(link, feed)
    if (note == DataNote.NONE || note == DataNote.WAITING) return
    val ctx = LocalContext.current
    val now = rememberNow(5_000L)
    val at = FeedStatusRules.dataAt(feed)
    val age = at?.let { Fmt.age(ctx, (now - it).coerceAtLeast(0) / 1000) }
    val text = when (note) {
        DataNote.CACHE -> if (age != null) stringResource(R.string.data_cache, age) else stringResource(R.string.data_cache_unknown)
        DataNote.OFFLINE -> if (age != null) stringResource(R.string.data_offline, age) else stringResource(R.string.data_offline_none)
        DataNote.BUSY -> if (age != null) stringResource(R.string.data_busy, age) else stringResource(R.string.data_busy_none)
        else -> if (age != null) stringResource(R.string.data_unreachable, age) else stringResource(R.string.data_unreachable_none)
    }
    val title = stringResource(
        when (note) {
            DataNote.CACHE -> R.string.data_cache_title
            DataNote.OFFLINE -> R.string.data_offline_title
            DataNote.BUSY -> R.string.data_busy_title
            else -> R.string.data_unreachable_title
        },
    )
    Banner(text, Tone.WATCH, modifier, title = title)
}
