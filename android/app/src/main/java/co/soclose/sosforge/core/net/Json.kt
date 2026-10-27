package co.soclose.sosforge.core.net

import kotlinx.serialization.json.Json

/** One lenient decoder for everything the server sends: unknown keys are ignored, never fatal. */
val SosJson: Json = Json {
    ignoreUnknownKeys = true
    isLenient = true
    coerceInputValues = true
    explicitNulls = false
}
