package co.soclose.sosforge.feature.sources

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.net.ApiError
import co.soclose.sosforge.core.net.ApiException
import co.soclose.sosforge.core.net.Iso
import co.soclose.sosforge.core.net.SourceHealth
import co.soclose.sosforge.feature.common.rememberLink
import co.soclose.sosforge.ui.kit.Banner
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.KeyValue
import co.soclose.sosforge.ui.kit.LinkPill
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.ShowMore
import co.soclose.sosforge.ui.kit.StatusPill
import co.soclose.sosforge.ui.kit.Tone
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/**
 * Sources: the REAL state of every one (`/api/sources`), its last error included. The REST read is
 * re-done every 30 s while the screen is visible; between reads the ticks keep it current. A failed
 * read keeps what was known and says how old it is.
 */
@Composable
fun SourcesScreen() {
    val ctx = LocalContext.current
    val app = ctx.app
    val feed by app.feed.collectAsStateWithLifecycle()
    val link = rememberLink()
    val owner = LocalLifecycleOwner.current
    var rest by remember { mutableStateOf<List<SourceHealth>?>(null) }
    var restAt by remember { mutableLongStateOf(0L) }
    var error by remember { mutableStateOf<ApiError?>(null) }
    val deviceNow = rememberNow(5_000L)
    val now = feed.serverNow(deviceNow)

    LaunchedEffect(Unit) {
        // screenshots render the verbatim tick copy; they never reach the network
        if (co.soclose.sosforge.ui.kit.ScreensMode.on) return@LaunchedEffect
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                try {
                    val r = withContext(Dispatchers.IO) { app.api().sources() }
                    rest = r.sources
                    restAt = System.currentTimeMillis()
                    error = null
                } catch (e: ApiException) {
                    error = e.error
                }
                delay(30_000L)
            }
        }
    }

    // the REST copy carries `mode`; the tick copy is newer between reads: merge by name
    val tick = feed.sources.associateBy { it.name }
    val sources = (rest ?: feed.sources).map { s -> tick[s.name]?.copy(mode = s.mode ?: tick[s.name]?.mode) ?: s }
    val up = sources.count { it.connected }

    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(horizontal = Space.gutter, vertical = Space.s), verticalArrangement = Arrangement.spacedBy(Space.s)) {
        item(key = "hero") {
            PageHero(
                title = stringResource(R.string.sources_title),
                overline = stringResource(R.string.sources_overline),
                sentence = if (sources.isEmpty()) stringResource(R.string.sources_none) else stringResource(R.string.sources_sentence, up, sources.size),
                trailing = { LinkPill(link) },
            )
        }
        if (error != null) item(key = "err") {
            val age = if (restAt > 0) Fmt.age(ctx, (deviceNow - restAt) / 1000) else null
            Banner(
                if (error == ApiError.Unreachable) {
                    if (age != null) stringResource(R.string.sources_unreachable_age, age) else stringResource(R.string.sources_unreachable)
                } else stringResource(R.string.sources_failed),
                Tone.WATCH,
            )
        }
        items(sources.sortedWith(compareBy<SourceHealth> { it.connected }.thenBy { Words.source(it.name) }), key = { it.name }) { s ->
            SourceCard(s, now)
        }
    }
}

@Composable
private fun SourceCard(s: SourceHealth, serverNow: Long) {
    val ctx = LocalContext.current
    Card {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(Words.source(s.name), style = Sos.type.heading, color = Sos.colors.t1, maxLines = 2, overflow = TextOverflow.Ellipsis)
                s.mode?.let { Text(it, style = Sos.type.meta, color = Sos.colors.t3) }
            }
            Spacer(Modifier.width(Space.s))
            StatusPill(stringResource(if (s.connected) R.string.source_up else R.string.source_down), if (s.connected) Tone.OK else Tone.ALERT)
        }
        Spacer(Modifier.height(Space.xs))
        val ok = Iso.ms(s.lastOk)
        KeyValue(stringResource(R.string.source_last_ok), ok?.let { Fmt.age(ctx, (serverNow - it) / 1000) } ?: stringResource(R.string.source_never))
        KeyValue(stringResource(R.string.source_seen), s.eventsSeen.toString())
        s.ingested?.let { KeyValue(stringResource(R.string.source_ingested), it.toString()) }
        KeyValue(stringResource(R.string.source_errors), s.errors.toString())
        if (!s.lastError.isNullOrBlank()) {
            Text(stringResource(R.string.source_last_error), style = Sos.type.small, color = Sos.colors.t2)
            ShowMore(s.lastError, collapsedLines = 1, mono = true)
        }
    }
}
