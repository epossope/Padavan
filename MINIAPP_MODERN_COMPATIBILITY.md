# Noema Mini App modern compatibility contract

## Production JavaScript target

The Mini App is shipped as direct JavaScript (there is no bundler or transpiler).
Its supported syntax target is ES2020: current and reasonably recent Telegram
WKWebView and Android System WebView. The separate pre-boot layer in
`miniapp/index.html` is deliberately ES5 so that it can report a controlled
failure even when an application asset cannot parse in an older runtime.

Runtime APIs are not inferred from syntax support. Startup has explicit
fallbacks for absent `AbortController`, `visualViewport`, `ResizeObserver`,
and `IntersectionObserver`; Telegram methods are guarded at each call site.
Voice, speech synthesis, media capture, fullscreen, clipboard, and realtime
voice are optional product capabilities and never gate Home.

## Boot and cache contract

`/app` is `Cache-Control: no-store`. Every static asset is versioned with the
canonical server `APP_BUILD_ID`; a matching version is immutable for one year,
while an unrecognised version is revalidated. A mismatch can trigger one
session-scoped cache-busted reload. Any repeat, script load/parse error, SDK
failure, early exception, authentication failure, or bounded boot timeout
shows the standalone Russian recovery screen instead of a spinner.

The critical boot path is: pre-boot, application scripts, Telegram initData,
and the signed minimum `state` request. Weather, budget, widgets, speech, and
realtime assets run after Home and are non-critical.

## Device acceptance

Automated checks establish recovery behavior, but real Telegram acceptance is
still required on at least two current iPhone users/devices and two current
Android users/devices, including cold start, reopen, Wi-Fi/mobile data, and a
deliberate network failure. This is capability-based; no hardware-model branch
is permitted.
