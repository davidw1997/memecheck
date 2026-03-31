console.log("MemeCheck premium UI loaded.");

document.addEventListener("DOMContentLoaded", function () {
    const container = document.getElementById("tv-widget-container");
    if (!container) return;

    const symbol = container.dataset.symbol;

    if (!symbol) return;

    if (window.TradingView) {
        new TradingView.widget({
            autosize: true,
            symbol: symbol,
            interval: "15",
            timezone: "Etc/UTC",
            theme: "dark",
            style: "1",
            locale: "en",
            allow_symbol_change: false,
            hide_top_toolbar: false,
            hide_legend: false,
            save_image: false,
            container_id: "tv-widget-container"
        });
        return;
    }

    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/tv.js";
    script.onload = function () {
        if (!window.TradingView) return;
        new TradingView.widget({
            autosize: true,
            symbol: symbol,
            interval: "15",
            timezone: "Etc/UTC",
            theme: "dark",
            style: "1",
            locale: "en",
            allow_symbol_change: false,
            hide_top_toolbar: false,
            hide_legend: false,
            save_image: false,
            container_id: "tv-widget-container"
        });
    };
    document.body.appendChild(script);
});
