package co.soclose.sosforge.feature.settings

import androidx.appcompat.app.AppCompatDelegate
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.core.os.LocaleListCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import co.soclose.sosforge.BuildConfig
import co.soclose.sosforge.R
import co.soclose.sosforge.app
import co.soclose.sosforge.core.Fmt
import co.soclose.sosforge.core.Prefs
import co.soclose.sosforge.core.TextSize
import co.soclose.sosforge.core.Words
import co.soclose.sosforge.core.alert.Notifier
import co.soclose.sosforge.core.net.Kind
import co.soclose.sosforge.core.net.ServerAddress
import co.soclose.sosforge.core.net.Severity
import co.soclose.sosforge.core.sys.Diagnostics
import co.soclose.sosforge.core.watch.WatchMode
import co.soclose.sosforge.core.watch.WatchRules
import co.soclose.sosforge.feature.common.rememberLink
import co.soclose.sosforge.ui.kit.Card
import co.soclose.sosforge.ui.kit.Chip
import co.soclose.sosforge.ui.kit.ChipFlow
import co.soclose.sosforge.ui.kit.KeyValue
import co.soclose.sosforge.ui.kit.PageHero
import co.soclose.sosforge.ui.kit.SecondaryButton
import co.soclose.sosforge.ui.kit.Section
import co.soclose.sosforge.ui.kit.ShowMore
import co.soclose.sosforge.ui.kit.TextAction
import co.soclose.sosforge.ui.kit.navFor
import co.soclose.sosforge.ui.kit.rememberNow
import co.soclose.sosforge.ui.kit.twoColumns
import co.soclose.sosforge.ui.kit.windowDp
import co.soclose.sosforge.ui.theme.Sos
import co.soclose.sosforge.ui.theme.Space
import co.soclose.sosforge.ui.theme.ThemeMode
import kotlinx.coroutines.launch

/** The five languages of the web client (frontend/src/i18n.ts), by their own names. */
val LANGS = listOf("en" to "English", "fr" to "Français", "es" to "Español", "ja" to "日本語", "id" to "Indonesia")

@Composable
fun SettingsScreen(onSetup: () -> Unit) {
    val ctx = LocalContext.current
    val app = ctx.app
    val prefs = app.prefs
    val places by prefs.places.collectAsStateWithLifecycle()
    val alerts by prefs.alerts.collectAsStateWithLifecycle()
    val quiet by prefs.quiet.collectAsStateWithLifecycle()
    val watchLive by prefs.watchLive.collectAsStateWithLifecycle()
    val running by app.watchRunning.collectAsStateWithLifecycle()
    val theme by prefs.theme.collectAsStateWithLifecycle()
    val textSize by prefs.textSize.collectAsStateWithLifecycle()
    val origin by prefs.origin.collectAsStateWithLifecycle()
    val sock by app.live.state.collectAsStateWithLifecycle()
    val version by app.version.collectAsStateWithLifecycle()
    val status by app.alerts.status.collectAsStateWithLifecycle()
    val packs by app.offline.packs.collectAsStateWithLifecycle()
    val unmetered by app.unmetered.collectAsStateWithLifecycle()
    val update by app.update.collectAsStateWithLifecycle()
    val here by prefs.here.collectAsStateWithLifecycle()
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val link = rememberLink()
    val now = rememberNow(5_000L)
    val (w, h) = windowDp()
    val two = twoColumns(w)

    val left: @Composable () -> Unit = {
        Section(stringResource(R.string.set_alerts)) {
            Card {
                Text(
                    stringResource(R.string.set_alerts_summary, stringResource(Words.severity(alerts.floor)), alerts.kinds.size, Kind.entries.size),
                    style = Sos.type.body, color = Sos.colors.t1,
                )
                Text(pluralStringResource(R.plurals.places_count, places.size, places.size), style = Sos.type.small, color = Sos.colors.t2)
                TextAction(stringResource(R.string.set_run_setup), onSetup)
            }
            Card {
                SwitchRow(stringResource(R.string.set_quiet), quiet.enabled) { prefs.setQuiet(quiet.copy(enabled = it)) }
                Text(stringResource(R.string.set_quiet_rule), style = Sos.type.small, color = Sos.colors.t2)
                if (quiet.enabled) {
                    Text(stringResource(R.string.set_quiet_from), style = Sos.type.small, color = Sos.colors.t2, modifier = Modifier.padding(top = Space.s))
                    ChipFlow { listOf(21 * 60, 22 * 60, 23 * 60, 0).forEach { m -> Chip(Fmt.hm(m), quiet.fromMin == m, { prefs.setQuiet(quiet.copy(fromMin = m)) }) } }
                    Text(stringResource(R.string.set_quiet_to), style = Sos.type.small, color = Sos.colors.t2)
                    ChipFlow { listOf(6 * 60, 7 * 60, 8 * 60, 9 * 60).forEach { m -> Chip(Fmt.hm(m), quiet.toMin == m, { prefs.setQuiet(quiet.copy(toMin = m)) }) } }
                    if (status.held > 0) Text(pluralStringResource(R.plurals.set_quiet_held, status.held, status.held, Fmt.hm(quiet.toMin)), style = Sos.type.small, color = Sos.colors.amber)
                }
            }
        }

        Section(stringResource(R.string.set_watch)) {
            Card {
                val mode = WatchRules.mode(places.isNotEmpty(), watchLive, running)
                Text(
                    stringResource(
                        when (mode) {
                            WatchMode.LIVE -> R.string.watch_mode_live
                            WatchMode.PERIODIC -> R.string.watch_mode_periodic
                            WatchMode.LIVE_NOT_RUNNING -> R.string.watch_mode_not_running
                            WatchMode.OFF -> R.string.watch_mode_off
                        },
                    ),
                    style = Sos.type.bodyStrong,
                    color = if (mode == WatchMode.LIVE_NOT_RUNNING) Sos.colors.amber else Sos.colors.t1,
                )
                Spacer(Modifier.height(Space.s))
                SwitchRow(stringResource(R.string.set_watch_live), watchLive, enabled = places.isNotEmpty()) { app.watch.setLive(it) }
                val exempt = app.watch.batteryExempt()
                KeyValue(stringResource(R.string.set_battery), stringResource(if (exempt) R.string.set_battery_exempt else R.string.set_battery_optimised))
                if (!exempt) TextAction(stringResource(R.string.set_battery_allow), { runCatching { ctx.startActivity(Diagnostics.batteryExemption(ctx)) } })
                val pollAt = prefs.getLong(Prefs.K_POLL_AT)
                KeyValue(
                    stringResource(R.string.set_last_check),
                    if (pollAt > 0) Fmt.age(ctx, (now - pollAt) / 1000) + " · " + (prefs.getString(Prefs.K_POLL_RESULT) ?: "") else stringResource(R.string.set_never),
                )
            }
        }
    }

    val right: @Composable () -> Unit = {
        Section(stringResource(R.string.set_offline)) {
            Card {
                OfflineSummary(packs, places, unmetered)
            }
        }

        Section(stringResource(R.string.set_updates)) {
            Card {
                Text(
                    when (val u = update) {
                        co.soclose.sosforge.core.update.UpdateState.NotChecked -> stringResource(R.string.update_not_checked)
                        co.soclose.sosforge.core.update.UpdateState.UpToDate -> stringResource(R.string.update_up_to_date, BuildConfig.VERSION_NAME)
                        co.soclose.sosforge.core.update.UpdateState.NonePublished -> stringResource(R.string.update_none_published)
                        co.soclose.sosforge.core.update.UpdateState.Unreachable -> stringResource(R.string.update_unreachable)
                        is co.soclose.sosforge.core.update.UpdateState.Refused -> stringResource(R.string.update_refused)
                        is co.soclose.sosforge.core.update.UpdateState.Available -> stringResource(R.string.update_body, u.release.version)
                    },
                    style = Sos.type.body, color = Sos.colors.t1,
                )
                MetaRowCompat {
                    val u = update
                    if (u is co.soclose.sosforge.core.update.UpdateState.Available) {
                        TextAction(stringResource(R.string.update_download), { co.soclose.sosforge.feature.detail.openUrl(ctx, u.downloadUrl) })
                    }
                    TextAction(stringResource(R.string.update_check_now), { scope.launch { runCatching { app.checkUpdate(force = true) } } })
                }
            }
        }

        Section(stringResource(R.string.set_display)) {
            Card {
                Text(stringResource(R.string.set_language), style = Sos.type.small, color = Sos.colors.t2)
                val current = AppCompatDelegate.getApplicationLocales().toLanguageTags()
                ChipFlow {
                    Chip(stringResource(R.string.set_system), current.isEmpty(), { AppCompatDelegate.setApplicationLocales(LocaleListCompat.getEmptyLocaleList()) })
                    LANGS.forEach { (tag, name) ->
                        Chip(name, current.startsWith(tag) || (tag == "id" && current.startsWith("in")), { AppCompatDelegate.setApplicationLocales(LocaleListCompat.forLanguageTags(tag)) })
                    }
                }
                Text(stringResource(R.string.set_theme), style = Sos.type.small, color = Sos.colors.t2, modifier = Modifier.padding(top = Space.s))
                ChipFlow {
                    Chip(stringResource(R.string.set_system), theme == ThemeMode.SYSTEM, { prefs.setTheme(ThemeMode.SYSTEM) })
                    Chip(stringResource(R.string.theme_lunaire), theme == ThemeMode.LUNAIRE, { prefs.setTheme(ThemeMode.LUNAIRE) })
                    Chip(stringResource(R.string.theme_doux), theme == ThemeMode.DOUX, { prefs.setTheme(ThemeMode.DOUX) })
                }
                Text(stringResource(R.string.set_text_size), style = Sos.type.small, color = Sos.colors.t2, modifier = Modifier.padding(top = Space.s))
                ChipFlow {
                    TextSize.entries.forEach { t ->
                        Chip(stringResource(textLabel(t)), textSize == t, { prefs.setTextSize(t) })
                    }
                }
            }
        }

        Section(stringResource(R.string.set_server)) {
            Card {
                if (co.soclose.sosforge.core.net.DevHosts.OVERRIDE) ServerEditor(origin)
                else KeyValue(stringResource(R.string.set_server_label), ServerAddress.hostOf(origin))
            }
        }

        Section(stringResource(R.string.set_diagnostics)) {
            Card {
                val notifier = app.notifier
                KeyValue(stringResource(R.string.diag_notif_permission), stringResource(if (Diagnostics.notificationPermission(ctx)) R.string.diag_granted else R.string.diag_refused))
                KeyValue(stringResource(R.string.diag_appops), Diagnostics.notificationAppOp(ctx))
                KeyValue(stringResource(R.string.ch_extreme_name), Diagnostics.importanceWord(notifier.channelImportance(Notifier.CH_EXTREME)))
                KeyValue(stringResource(R.string.ch_severe_name), Diagnostics.importanceWord(notifier.channelImportance(Notifier.CH_SEVERE)))
                KeyValue(stringResource(R.string.ch_digest_name), Diagnostics.importanceWord(notifier.channelImportance(Notifier.CH_DIGEST)))
                if (status.blocked > 0) Text(pluralStringResource(R.plurals.diag_blocked, status.blocked, status.blocked), style = Sos.type.small, color = Sos.colors.amber)
                TextAction(stringResource(R.string.diag_open_notif_settings), { runCatching { ctx.startActivity(Diagnostics.notificationSettings(ctx)) } })
                val nav = navFor(w, h)
                KeyValue(
                    stringResource(R.string.diag_window),
                    "$w x $h dp · " + stringResource(if (nav.place == co.soclose.sosforge.ui.kit.NavPlace.BOTTOM_BAR) R.string.diag_bar else R.string.diag_rail) +
                        " · " + stringResource(if (two) R.string.diag_two_columns else R.string.diag_one_column),
                )
                KeyValue(stringResource(R.string.diag_font_scale), "%.2f".format(LocalDensity.current.fontScale))
                KeyValue(
                    stringResource(R.string.diag_location),
                    stringResource(
                        when {
                            !co.soclose.sosforge.core.sys.Locate.granted(ctx) -> R.string.diag_location_not_granted
                            !co.soclose.sosforge.core.sys.Locate.locationEnabled(ctx) -> R.string.diag_location_off
                            else -> R.string.diag_granted
                        },
                    ),
                )
                here?.let { h -> KeyValue(stringResource(R.string.diag_here), Fmt.latLon(h.lat, h.lon) + " \u00B7 " + Fmt.age(ctx, (now - h.at).coerceAtLeast(0) / 1000)) }
                KeyValue(stringResource(R.string.diag_link), link.name.lowercase())
                KeyValue(stringResource(R.string.diag_server_features), version?.features?.joinToString(", ")?.ifBlank { null } ?: stringResource(if (version == null) R.string.diag_unknown else R.string.diag_legacy))
                KeyValue(stringResource(R.string.diag_watch_running), stringResource(if (running) R.string.diag_yes else R.string.diag_no))
                prefs.getString(Prefs.K_WATCH_ERROR)?.let { KeyValue(stringResource(R.string.diag_watch_error), it) }
                KeyValue(stringResource(R.string.diag_alerts), "${status.posted} / ${status.held}")
                KeyValue(stringResource(R.string.diag_version), "${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
                val socket = listOfNotNull(
                    "attempt ${sock.attempt}",
                    sock.lastError?.let { "last: $it" },
                    sock.query.takeIf { it.isNotBlank() }?.let { "scope: $it" },
                    sock.refused?.let { "refused: $it" },
                ).joinToString("\n")
                Text(stringResource(R.string.diag_socket), style = Sos.type.small, color = Sos.colors.t2, modifier = Modifier.padding(top = Space.s))
                ShowMore(socket, collapsedLines = 1, mono = true)
            }
        }
        Spacer(Modifier.height(Space.l))
        Text(stringResource(R.string.about_credit), style = Sos.type.meta, color = Sos.colors.t3)
        Spacer(Modifier.height(Space.xxl))
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = Space.gutter, vertical = Space.s)) {
        PageHero(title = stringResource(R.string.set_title), overline = stringResource(R.string.set_overline))
        if (two) {
            Row(horizontalArrangement = Arrangement.spacedBy(Space.l)) {
                Column(Modifier.weight(1f)) { left() }
                Column(Modifier.weight(1f)) { right() }
            }
        } else {
            left()
            right()
        }
    }
}

fun textLabel(t: TextSize): Int = when (t) {
    TextSize.SMALL -> R.string.text_small
    TextSize.DEFAULT -> R.string.text_default
    TextSize.LARGE -> R.string.text_large
    TextSize.XLARGE -> R.string.text_xlarge
}

@Composable
fun SwitchRow(label: String, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = Sos.type.bodyStrong, color = Sos.colors.t1, modifier = Modifier.weight(1f))
        Spacer(Modifier.width(Space.s))
        Switch(
            checked = checked, onCheckedChange = onChange, enabled = enabled,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Sos.colors.onPrimary, checkedTrackColor = Sos.colors.mint,
                uncheckedThumbColor = Sos.colors.t2, uncheckedTrackColor = Sos.colors.bg4, uncheckedBorderColor = Sos.colors.lineStrong,
            ),
        )
    }
}

/** Severity floor chips, shared with the funnel. */
@Composable
fun SeverityFloorChips(selected: Severity, onPick: (Severity) -> Unit) {
    ChipFlow {
        listOf(Severity.MINOR, Severity.MODERATE, Severity.SEVERE, Severity.EXTREME).forEach { s ->
            Chip(stringResource(Words.severity(s)), selected == s, { onPick(s) }, leading = { co.soclose.sosforge.ui.kit.SeverityGlyph(s, size = 10.dp, color = if (selected == s) Sos.colors.onPrimary else null) })
        }
    }
}


@Composable
private fun MetaRowCompat(content: @Composable androidx.compose.foundation.layout.FlowRowScope.() -> Unit) = co.soclose.sosforge.ui.kit.MetaRow(content = content)

/** The offline packs, one line per place (both styles summed), plus the world. */
@Composable
private fun OfflineSummary(packs: Map<String, co.soclose.sosforge.core.map.PackState>, places: List<co.soclose.sosforge.core.geo.Place>, unmetered: Boolean) {
    val groups = packs.values.groupBy { it.placeId }
    fun line(list: List<co.soclose.sosforge.core.map.PackState>): String? {
        if (list.isEmpty()) return null
        val done = list.all { it.complete }
        val req = list.sumOf { it.required }.coerceAtLeast(1)
        val got = list.sumOf { it.completed }
        val mb = list.sumOf { it.bytes } / 1_000_000.0
        return if (done) "%.0f\u00A0MB \u00B7 ok".format(mb) else "%d\u00A0%% \u00B7 %.0f\u00A0MB".format((got * 100 / req).coerceIn(0, 99), mb)
    }
    Text(stringResource(if (unmetered) R.string.offline_rule_now else R.string.offline_rule_waiting), style = Sos.type.small, color = if (unmetered) Sos.colors.t2 else Sos.colors.amber)
    KeyValue(stringResource(R.string.offline_world), line(groups[null].orEmpty()) ?: stringResource(R.string.offline_not_started))
    places.forEach { p -> KeyValue(p.name, line(groups[p.id].orEmpty()) ?: stringResource(R.string.offline_not_started)) }
    packs.values.firstOrNull { it.error != null }?.let { Text(it.error!!, style = Sos.type.meta, color = Sos.colors.amber, maxLines = 2) }
}

/** The server address editor: DEBUG builds only (a release has a constant false here, so none of this ships). */
@Composable
private fun ServerEditor(origin: String) {
    val prefs = LocalContext.current.app.prefs
                var draft by rememberSaveable(origin) { mutableStateOf(origin) }
                var bad by remember { mutableStateOf(false) }
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it.take(200); bad = false },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    label = { Text(stringResource(R.string.set_server_label)) },
                    isError = bad,
                    supportingText = { Text(stringResource(if (bad) R.string.set_server_bad else R.string.set_server_hint)) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = Sos.colors.t1, unfocusedBorderColor = Sos.colors.lineStrong,
                        focusedLabelColor = Sos.colors.t2, unfocusedLabelColor = Sos.colors.t3, cursorColor = Sos.colors.t1,
                    ),
                    textStyle = Sos.type.mono.copy(color = Sos.colors.t1),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(Space.s), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton(stringResource(R.string.save), {
                        val n = ServerAddress.normalize(draft)
                        if (n == null) bad = true else { prefs.setOrigin(n); draft = n }
                    })
                    if (origin != ServerAddress.DEFAULT) TextAction(stringResource(R.string.set_server_default), { prefs.setOrigin(ServerAddress.DEFAULT); draft = ServerAddress.DEFAULT })
                }
}
