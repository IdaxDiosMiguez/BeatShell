import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.chaquo.python")
}

// Firma: si hay un keystore en las variables de entorno (secretos de CI) se usa ese;
// si no, el keystore de depuración del SDK. Ver android/README.md.
val releaseKeystore: File? = System.getenv("BEATSHELL_KEYSTORE_BASE64")?.takeIf { it.isNotBlank() }?.let {
    layout.buildDirectory.file("release.keystore").get().asFile.apply {
        parentFile.mkdirs()
        writeBytes(Base64.getMimeDecoder().decode(it))
    }
}

android {
    namespace = "io.github.idaxdiosmiguez.beatshell"
    compileSdk {
        version = release(37) { minorApiLevel = 2 }
    }

    defaultConfig {
        applicationId = "io.github.idaxdiosmiguez.beatshell"
        minSdk = 24
        targetSdk = 36
        versionCode = (System.getenv("BEATSHELL_VERSION_CODE") ?: "1").toInt()
        versionName = "0.1.0"
        ndk {
            // Teléfonos de 64 bits (casi todos) y emuladores x86_64.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (releaseKeystore != null) {
            create("release") {
                storeFile = releaseKeystore
                storePassword = System.getenv("BEATSHELL_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("BEATSHELL_KEY_ALIAS")
                keyPassword = System.getenv("BEATSHELL_KEY_PASSWORD") ?: System.getenv("BEATSHELL_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            // yt-dlp: extracción de YouTube (Unlicense). mutagen: etiquetas de las descargas (GPL-2.0+).
            install("yt-dlp==2026.11.23")
            install("mutagen==1.47.0")
        }
    }
    sourceSets {
        getByName("main") {
            // El núcleo de BeatShell (src/BeatShell) se reutiliza tal cual.
            srcDir("../../src")
        }
    }
}

dependencies {
    val media3 = "1.11.1"
    implementation("androidx.media3:media3-exoplayer:$media3")
    implementation("androidx.media3:media3-session:$media3")
    implementation("androidx.media3:media3-common-ktx:$media3")

    implementation(platform("androidx.compose:compose-bom:2026.11.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.core:core-ktx:1.19.1")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.11.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.11.1")
    implementation("androidx.lifecycle:lifecycle-service:2.11.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-guava:1.11.0")

    implementation("io.coil-kt.coil3:coil-compose:3.6.3")
    implementation("io.coil-kt.coil3:coil-network-okhttp:3.6.3")
}
