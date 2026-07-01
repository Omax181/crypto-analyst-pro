/**
 * Relais Telegram → GitHub Actions pour Crypto Analyst Pro (v18.1).
 *
 * Objectif : réveiller le bot UNIQUEMENT quand Omar envoie un message (au lieu
 * d'un cron toutes les 5 min qui gaspille le quota GitHub Actions).
 *
 * Flux :
 *   1. Telegram POST le message sur ce Worker (webhook).
 *   2. Le Worker valide le secret + le chat_id d'Omar, empile l'update dans une
 *      file KV (TG_QUEUE), puis déclenche un `repository_dispatch` GitHub.
 *   3. Le run GitHub draine la file via GET /pull (authentifié) et répond.
 *
 * La file KV garantit qu'AUCUN message n'est perdu même si plusieurs arrivent
 * pendant qu'un run tourne déjà (la concurrency GitHub sérialise les runs ; le
 * prochain run draine tout ce qui reste). Idempotence : la clé KV = update_id,
 * donc une re-livraison Telegram n'ajoute pas de doublon.
 *
 * --- Configuration (tableau de bord Cloudflare) -----------------------------
 * Variables (Settings → Variables) :
 *   GH_OWNER         = Omax181
 *   GH_REPO          = crypto-analyst-pro
 *   ALLOWED_CHAT_ID  = 7311655046
 * Secrets (Settings → Variables → Encrypt) :
 *   RELAY_SECRET     = (un mot de passe long que TU choisis ; sert au webhook
 *                       Telegram ET au GET /pull du bot)
 *   GH_DISPATCH_TOKEN= (GitHub Fine-grained PAT, repo crypto-analyst-pro,
 *                       permission « Contents: Read and write »)
 * Binding KV (Settings → Variables → KV Namespace Bindings) :
 *   TG_QUEUE         → un namespace KV que tu crées (ex. « tg-queue »)
 * ---------------------------------------------------------------------------
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // ---- Route /pull : le bot GitHub draine la file -----------------------
    if (url.pathname === "/pull") {
      if (!authorized(request, env)) {
        return json({ error: "unauthorized" }, 401);
      }
      const updates = await drainQueue(env);
      return json({ updates });
    }

    // ---- Route /tg : webhook Telegram -------------------------------------
    if (url.pathname === "/tg" && request.method === "POST") {
      // Telegram envoie le secret dans cet en-tête (défini via setWebhook).
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (secret !== env.RELAY_SECRET) {
        return json({ error: "forbidden" }, 403);
      }

      let update;
      try {
        update = await request.json();
      } catch (_e) {
        return json({ ok: true }); // corps illisible : on ignore proprement.
      }

      const msg = update && update.message;
      const chatId = msg && msg.chat && msg.chat.id;
      // Sécurité : on ne sert QUE le chat d'Omar. Tout le reste est ignoré.
      if (msg && String(chatId) === String(env.ALLOWED_CHAT_ID) && msg.text) {
        const id = update.update_id != null ? update.update_id : Date.now();
        // TTL 1 jour : un orphelin (jamais drainé) s'auto-nettoie.
        await env.TG_QUEUE.put(`upd:${id}`, JSON.stringify(update), {
          expirationTtl: 86400,
        });
        // Réveil du bot (best-effort : si ça échoue, le prochain message draine).
        await triggerDispatch(env);
      }
      // On répond toujours 200 pour éviter que Telegram ne renvoie en boucle.
      return json({ ok: true });
    }

    // Healthcheck simple.
    return new Response("cap-telegram-relay ok", { status: 200 });
  },
};

function authorized(request, env) {
  const auth = request.headers.get("Authorization") || "";
  return auth === `Bearer ${env.RELAY_SECRET}`;
}

async function drainQueue(env) {
  const updates = [];
  const list = await env.TG_QUEUE.list({ prefix: "upd:" });
  for (const key of list.keys) {
    const raw = await env.TG_QUEUE.get(key.name);
    await env.TG_QUEUE.delete(key.name);
    if (raw) {
      try {
        updates.push(JSON.parse(raw));
      } catch (_e) {
        // entrée corrompue : on l'a déjà supprimée, on continue.
      }
    }
  }
  return updates;
}

async function triggerDispatch(env) {
  try {
    await fetch(
      `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "cap-telegram-relay",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ event_type: "telegram" }),
      }
    );
  } catch (_e) {
    // best-effort : un échec de dispatch n'empêche pas la mise en file.
  }
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
