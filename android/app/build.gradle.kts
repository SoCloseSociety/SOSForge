import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

val versionProps = Properties().apply { rootProject.file("version.properties").inputStream().use { load(it) } }

// The release key lives OUTSIDE the repo (~/.soclose/android/sosforge.properties + sosforge-release.jks).
val keystoreProps = Properties().apply {
    val f = File(System.getProperty("user.home"), ".soclose/android/sosforge.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "co.soclose.sosforge"
    compileSdk = 36

    defaultConfig {
        applicationId = "co.soclose.sosforge"
        minSdk = 26
        targetSdk = 36
        versionCode = versionProps.getProperty("versionCode").toInt()
        versionName = versionProps.getProperty("versionName")
        // The five languages of the web client (frontend/src/i18n.ts): en, fr, es, ja, id ("in" on Android).
        resourceConfigurations += listOf("en", "fr", "es", "ja", "in")
        // MapLibre ships native .so: phones (arm64, armv7) and the x86_64 emulators. No 32-bit x86.
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64") }
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.isNotEmpty()) {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            // A release must never silently fall back to the debug key (build.sh also refuses).
            if (keystoreProps.isNotEmpty()) signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
        // Screenshot tests render the real resources (fonts, strings, drawables) through Robolectric.
        unitTests.isIncludeAndroidResources = true
        unitTests.all {
            it.systemProperty("sos.screens", (project.findProperty("sos.screens") ?: "false").toString())
            it.systemProperty("sos.screens.out", layout.buildDirectory.dir("screens").get().asFile.absolutePath)
            it.systemProperty("robolectric.pixelCopyRenderMode", "hardware")
            it.maxHeapSize = "2g"
        }
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
        // MapLibre's .so files are 16 KB aligned: keep them uncompressed so the loader maps them directly.
        jniLibs.useLegacyPackaging = false
    }
    lint {
        abortOnError = true
        checkReleaseBuilds = true
    }
}

kotlin {
    compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.appcompat)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.service)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.work)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.compose.material.icons)
    // Adaptive (Fold): bar <-> rail from the window size.
    implementation(libs.compose.material3.navsuite)
    implementation(libs.compose.adaptive)
    implementation(libs.compose.ui.tooling.preview)
    debugImplementation(libs.compose.ui.tooling)
    implementation(libs.androidx.splashscreen)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.maplibre)

    testImplementation(libs.junit)
    testImplementation(libs.kotlinx.coroutines.test)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.robolectric)
    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.compose.ui.test.junit4)
    debugImplementation(libs.compose.ui.test.manifest)
}

// Screenshot tests (app/src/test/.../screens) run only on demand, on the debug variant (they need the
// ui-test manifest): ./gradlew :app:testDebugUnitTest -Psos.screens=true. Release tests never see them.
tasks.withType<Test>().configureEach {
    if (project.findProperty("sos.screens") != "true" || name.contains("Release")) exclude("**/screens/**")
}
