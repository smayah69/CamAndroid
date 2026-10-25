package com.camandroid.core

import java.io.File

/**
 * Runs the real [CamServer] with a synthetic camera, so the desktop client can be tested against
 * the Kotlin implementation. Usage: runFakePhone [port] [codeFile]
 * The pairing code is written to codeFile (and stdout) once the server is listening.
 */
fun main(args: Array<String>) {
    val port = args.getOrNull(0)?.toInt() ?: Protocol.DEFAULT_PORT
    val pairing = PairingManager(ttlMs = 60 * 60 * 1000L)
    val server = CamServer(TlsIdentity.generate(), InMemoryClientStore(), pairing, FakeFrameSource(), "Fake Phone",
        listener = object : CamServer.Listener {
            override fun onPaired(client: PairedClient) {
                println("paired ${client.name}")
                // Offer a fresh code so tests can exercise pairing more than once.
                pairing.newCode()
            }

            override fun onPairingFailed(reason: String) {
                println("pairing failed: $reason")
                pairing.newCode()
            }
        })
    pairing.onChange = { active -> active?.let { c -> args.getOrNull(1)?.let { File(it).writeText(c.code) } } }
    val bound = server.start(port)
    val code = pairing.newCode().code
    println("listening on $bound, pairing code ${Protocol.formatCode(code)}")
    Thread.currentThread().join()
}
