package pro.progvpn.app;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.net.VpnService;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

import com.wireguard.android.backend.Backend;
import com.wireguard.android.backend.GoBackend;
import com.wireguard.android.backend.Tunnel;
import com.wireguard.config.Config;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.StringReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * ProGVPN — нативный Android-клиент WireGuard.
 *
 * Умеет:
 *  - загрузить .conf из файла (системный выбор файлов) или по прямой ссылке;
 *  - поднять/опустить WireGuard-туннель одной кнопкой (встроенная библиотека
 *    WireGuard, без официального приложения и без ручного ввода).
 */
public class MainActivity extends Activity {

    private static final int REQ_VPN = 1001;
    private static final int REQ_FILE = 1002;
    private static final String CONF_NAME = "progvpn.conf";
    private static final String PREFS = "progvpn";
    private static final String PREF_URL = "config_url";

    private TextView statusView;
    private TextView configInfoView;
    private EditText urlInput;
    private Button connectButton;

    private Backend backend;
    private final WgTunnel tunnel = new WgTunnel();
    private final Handler ui = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        statusView = findViewById(R.id.status);
        configInfoView = findViewById(R.id.config_info);
        urlInput = findViewById(R.id.url_input);
        connectButton = findViewById(R.id.btn_connect);

        backend = new GoBackend(this);

        SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
        urlInput.setText(sp.getString(PREF_URL, ""));

        findViewById(R.id.btn_file).setOnClickListener(v -> pickFile());
        findViewById(R.id.btn_url).setOnClickListener(v -> fetchFromUrl());
        connectButton.setOnClickListener(v -> toggleConnection());

        updateConfigInfo();
        updateConnectButton();
    }

    // ------------------------- источник конфига -------------------------

    /** Выбор .conf через системный файловый менеджер (SAF). */
    private void pickFile() {
        Intent i = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        i.addCategory(Intent.CATEGORY_OPENABLE);
        i.setType("*/*");
        startActivityForResult(i, REQ_FILE);
    }

    /** Загрузка .conf по прямой ссылке. */
    private void fetchFromUrl() {
        final String url = urlInput.getText().toString().trim();
        if (url.isEmpty()) {
            setStatus("Вставь прямую ссылку на .conf");
            return;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putString(PREF_URL, url).apply();

        setStatus("Загружаю конфиг…");
        new Thread(() -> {
            try {
                String text = download(url);
                if (text == null || !text.contains("[Interface]")) {
                    postStatus("По ссылке нет валидного .conf");
                    return;
                }
                saveConfig(text);
                ui.post(() -> {
                    updateConfigInfo();
                    setStatus("Конфиг получен по ссылке ✅");
                });
            } catch (Exception e) {
                postStatus("Ошибка загрузки: " + e.getMessage());
            }
        }).start();
    }

    private String download(String urlStr) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(urlStr).openConnection();
        c.setConnectTimeout(10000);
        c.setReadTimeout(10000);
        c.setInstanceFollowRedirects(true);
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(
                new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
            }
        } finally {
            c.disconnect();
        }
        return sb.toString();
    }

    // ------------------------- подключение -------------------------

    private void toggleConnection() {
        if (readConfig() == null || readConfig().trim().isEmpty()) {
            setStatus("Сначала выбери .conf или укажи ссылку");
            return;
        }
        if (isActive()) {
            disconnect();
        } else {
            connect();
        }
    }

    private void connect() {
        // Первый раз Android спросит разрешение на VPN — это обязательно.
        Intent prep = VpnService.prepare(this);
        if (prep != null) {
            startActivityForResult(prep, REQ_VPN);
            return;
        }
        setStatus("Подключаю…");
        new Thread(() -> {
            try {
                Config cfg = Config.parse(new BufferedReader(
                        new StringReader(readConfig())));
                backend.setState(tunnel, Tunnel.State.UP, cfg);
                ui.post(() -> {
                    setStatus("Подключено ✅");
                    updateConnectButton();
                });
            } catch (Exception e) {
                ui.post(() -> {
                    setStatus("Ошибка: " + e.getMessage());
                    updateConnectButton();
                });
            }
        }).start();
    }

    private void disconnect() {
        setStatus("Отключаю…");
        new Thread(() -> {
            try {
                backend.setState(tunnel, Tunnel.State.DOWN, null);
            } catch (Exception ignored) {
                // туннель уже опущен
            }
            ui.post(() -> {
                setStatus("Отключено");
                updateConnectButton();
            });
        }).start();
    }

    private boolean isActive() {
        try {
            return backend.getState(tunnel) == Tunnel.State.UP;
        } catch (Exception e) {
            return false;
        }
    }

    // ------------------------- хранение конфига -------------------------

    private File confFile() {
        return new File(getFilesDir(), CONF_NAME);
    }

    private void saveConfig(String text) throws Exception {
        try (FileOutputStream fo = new FileOutputStream(confFile())) {
            fo.write(text.getBytes(StandardCharsets.UTF_8));
        }
    }

    private String readConfig() {
        File f = confFile();
        if (!f.exists()) {
            return null;
        }
        try (BufferedReader r = new BufferedReader(
                new InputStreamReader(new FileInputStream(f), StandardCharsets.UTF_8))) {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
            }
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private void updateConfigInfo() {
        File f = confFile();
        configInfoView.setText(f.exists()
                ? "Конфиг загружен (" + f.length() + " байт)"
                : getString(R.string.config_not_set));
    }

    private void updateConnectButton() {
        connectButton.setText(isActive()
                ? getString(R.string.disconnect)
                : getString(R.string.connect));
    }

    // ------------------------- системные колбэки -------------------------

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);

        if (requestCode == REQ_VPN) {
            if (resultCode == RESULT_OK) {
                connect();
            } else {
                setStatus("Разрешение VPN не выдано");
            }
            return;
        }

        if (requestCode == REQ_FILE && resultCode == RESULT_OK
                && data != null && data.getData() != null) {
            try {
                Uri uri = data.getData();
                StringBuilder sb = new StringBuilder();
                try (InputStream is = getContentResolver().openInputStream(uri);
                     BufferedReader r = new BufferedReader(
                             new InputStreamReader(is, StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = r.readLine()) != null) {
                        sb.append(line).append('\n');
                    }
                }
                String text = sb.toString();
                if (!text.contains("[Interface]")) {
                    setStatus("Это не похоже на WireGuard .conf");
                    return;
                }
                saveConfig(text);
                updateConfigInfo();
                setStatus("Файл загружен ✅");
            } catch (Exception e) {
                setStatus("Ошибка чтения файла: " + e.getMessage());
            }
        }
    }

    private void postStatus(String s) {
        ui.post(() -> setStatus(s));
    }

    private void setStatus(String s) {
        statusView.setText(s);
    }
}
