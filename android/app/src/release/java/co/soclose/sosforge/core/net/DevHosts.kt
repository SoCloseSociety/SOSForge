package co.soclose.sosforge.core.net

/**
 * RELEASE builds: no server override, no cleartext host. The app talks to https://sosforge.soclose.co only
 * (owner rule, 2026-10-27). tools/check-release.sh proves it on the APK (dex strings, network config).
 */
object DevHosts {
    const val OVERRIDE = false
    val CLEARTEXT: Set<String> = emptySet()
}
