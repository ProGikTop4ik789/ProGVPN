package pro.progvpn.app;

import com.wireguard.android.backend.Tunnel;

/**
 * Реализация туннеля WireGuard для нашей VPN-сессии.
 * Имя должно подходить под [a-zA-Z0-9_=+.-]{1,15}.
 */
public class WgTunnel implements Tunnel {

    @Override
    public String getName() {
        return "progvpn";
    }

    @Override
    public void onStateChange(State newState) {
        // Сюда приходит уведомление о смене состояния (UP/DOWN) — можно логировать.
    }
}
