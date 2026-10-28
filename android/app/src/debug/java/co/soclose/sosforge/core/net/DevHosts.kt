package co.soclose.sosforge.core.net

/**
 * DEBUG builds only (src/debug): the server address may be changed (the drills on the emulator, a local
 * `make dev-api`), and plain http may reach this phone, the emulator's host alias and the WireGuard hub.
 * The release copy of this object (src/release) is empty: a release talks to https://sosforge.soclose.co
 * and nothing else (owner rule, 2026-10-27: nothing fake, everything live and real).
 */
object DevHosts {
    const val OVERRIDE = true

    /** EXACTLY the `<domain>` list of src/debug/res/xml/network_security_config.xml (RulesTest compares the two). */
    val CLEARTEXT: Set<String> = setOf("localhost", "127.0.0.1", "10.0.2.2", "10.8.0.1")
}
