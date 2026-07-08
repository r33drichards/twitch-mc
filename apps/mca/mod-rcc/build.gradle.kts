// ---------------------------------------------------------------------------
// mca-rcc — pure-Java Fabric client mod, retargeted to MC 1.20.1 for
// ReconnectedCC. Baritone + Meteor dependencies removed: this build exposes
// only server-legal, input-synthesis primitives + world reads. The remote
// agent is the sole navigation brain.
// ---------------------------------------------------------------------------

plugins {
    id("fabric-loom") version "1.7.4"
    java
}

base { archivesName = property("archives_base_name") as String }
version = property("mod_version") as String
group = property("maven_group") as String

repositories {
    maven("https://maven.fabricmc.net/")
    mavenCentral()
}

dependencies {
    minecraft("com.mojang:minecraft:${property("minecraft_version")}")
    mappings("net.fabricmc:yarn:${property("yarn_mappings")}:v2")
    modImplementation("net.fabricmc:fabric-loader:${property("loader_version")}")
    modImplementation("net.fabricmc.fabric-api:fabric-api:${property("fabric_version")}")

    // NOTE: no baritone, no meteor-client. Intentionally gone.

    // Jackson for JSON. include() is non-transitive so list each artifact.
    implementation("com.fasterxml.jackson.core:jackson-databind:2.17.2")
    include("com.fasterxml.jackson.core:jackson-databind:2.17.2")
    include("com.fasterxml.jackson.core:jackson-core:2.17.2")
    include("com.fasterxml.jackson.core:jackson-annotations:2.17.2")

    // Luaj — small (~500 KB), pure-Java Lua 5.2 interpreter. Chosen over
    // standalone Nashorn (Nashorn was removed from the JDK in 15+; the standalone
    // artifact is larger and JS is off-theme) and GraalVM JS (tens of MB). Pure
    // Java means it runs on stock JDK 17 with no ScriptEngineManager dependency,
    // and Lua matches this project's CC/Lua heritage. Single jar, no transitives.
    implementation("org.luaj:luaj-jse:3.0.1")
    include("org.luaj:luaj-jse:3.0.1")

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.3")
}

// 1.20.1 runs on Java 17 (RCC uses Temurin 17).
java { toolchain { languageVersion = JavaLanguageVersion.of(17) } }

tasks.test { useJUnitPlatform() }

// ---------------------------------------------------------------------------
// OpenRPC spec generation (unchanged): Schema.java is the single source of
// truth for the JSON-RPC surface. Emits ../proto/mca-rcc-openrpc.json.
// ---------------------------------------------------------------------------
val generateOpenRpc by tasks.registering(JavaExec::class) {
    description = "Emit ../proto/mca-rcc-openrpc.json from Schema.java."
    group = "build"
    classpath = sourceSets["main"].compileClasspath +
            files(tasks.named("compileJava").get().outputs.files)
    mainClass.set("com.btone.c.schema.Schema")
    val out = file("../proto/mca-rcc-openrpc.json")
    args(out.absolutePath)
    inputs.files(sourceSets["main"].java.srcDirs.flatMap {
        fileTree(it).matching { include("**/schema/**") }
    })
    outputs.file(out)
    dependsOn("compileJava")
}

sourceSets["main"].resources.srcDir(file("../proto"))
tasks.named<ProcessResources>("processResources") {
    dependsOn(generateOpenRpc)
    // Expand ${version} in fabric.mod.json only. Scoped by filesMatching so
    // the OpenRPC JSON's `$ref` tokens are left untouched.
    inputs.property("version", project.version)
    filesMatching("fabric.mod.json") {
        expand("version" to project.version)
    }
}
