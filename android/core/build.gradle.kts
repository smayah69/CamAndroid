import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.kotlin.jvm)
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions { jvmTarget.set(JvmTarget.JVM_17) }
}

dependencies {
    implementation(libs.kotlinx.serialization.json)
    testImplementation(libs.kotlin.test.junit)
    testImplementation(libs.junit)
}

// Runs a phone emulator (fake camera) used by the desktop interop test in CI.
tasks.register<JavaExec>("runFakePhone") {
    group = "verification"
    description = "Runs a fake phone server for desktop interop testing."
    classpath = sourceSets["test"].runtimeClasspath
    mainClass.set("com.camandroid.core.FakePhoneKt")
    args = listOfNotNull(project.findProperty("fakePhoneArgs")?.toString())
        .flatMap { it.split(" ") }
}
