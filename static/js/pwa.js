/* ===========================================================================
   PharmaX — écran de démarrage, proposition d'installation et carrousel.
   Ce fichier est autonome : il ne dépend d'aucune bibliothèque.
   =========================================================================== */

(function () {
  'use strict';

  // ---------------------------------------------------------------------
  // 1. Écran de démarrage — mobile uniquement
  //    Affiché au premier chargement d'une session, puis retiré. On ne le
  //    rejoue pas à chaque navigation : ce serait pénible sur une
  //    application consultée dans l'urgence.
  // ---------------------------------------------------------------------
  var SEUIL_MOBILE = 900;   // doit correspondre au point de rupture de pwa.css

  function surMobile() {
    return window.matchMedia('(max-width: ' + SEUIL_MOBILE + 'px)').matches;
  }

  function gererSplash() {
    var splash = document.getElementById('pharmaxSplash');
    if (!splash) return;

    // Sur poste fixe, l'écran de démarrage est retiré sans jamais s'afficher
    if (!surMobile()) {
      splash.remove();
      return;
    }

    // Mémorisé durablement : l'écran ne se montre qu'à la toute première
    // ouverture sur cet appareil, pas à chaque session ni à chaque page.
    var dejaVu = false;
    try { dejaVu = !!localStorage.getItem('pharmax_splash_vu'); } catch (e) {}
    if (dejaVu) {
      splash.remove();
      return;
    }

    document.body.classList.add('splash-actif');
    try { localStorage.setItem('pharmax_splash_vu', '1'); } catch (e) {}

    var fermer = function () {
      splash.classList.add('splash-sortie');
      document.body.classList.remove('splash-actif');
      setTimeout(function () { splash.remove(); }, 500);
    };

    // On laisse l'animation se dérouler, sans jamais bloquer l'utilisateur
    var minimum = setTimeout(fermer, 1600);
    splash.addEventListener('click', function () {
      clearTimeout(minimum);
      fermer();
    });
  }

  // ---------------------------------------------------------------------
  // 2. Proposition d'installation
  //    Chrome et Edge émettent `beforeinstallprompt`. iOS ne l'implémente
  //    pas : on y affiche à la place la marche à suivre manuelle.
  // ---------------------------------------------------------------------
  var invite = null;

  function estInstallee() {
    return window.matchMedia('(display-mode: standalone)').matches
      || window.navigator.standalone === true;
  }

  function estIOS() {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent)
      && !window.MSStream;
  }

  function banniereRefusee() {
    var refus = localStorage.getItem('pharmax_install_refus');
    if (!refus) return false;
    // On ne represente la bannière qu'après deux semaines
    return (Date.now() - Number(refus)) < 14 * 24 * 3600 * 1000;
  }

  function construireBanniere(surIOS) {
    var banniere = document.createElement('div');
    banniere.className = 'pwa-banner';
    banniere.setAttribute('role', 'dialog');
    banniere.setAttribute('aria-label', "Installer l'application");

    banniere.innerHTML =
      '<img class="pwa-banner-logo" src="/static/img/logo-icon.png" alt="">' +
      '<div class="pwa-banner-texte">' +
        '<strong>Installer PharmaX</strong>' +
        '<small>' + (surIOS
          ? 'Appuyez sur Partager, puis « Sur l\'écran d\'accueil ».'
          : 'Accès immédiat aux pharmacies de garde, même en urgence.') +
        '</small>' +
      '</div>' +
      '<div class="pwa-banner-actions">' +
        (surIOS ? '' : '<button type="button" class="pwa-btn-installer">Installer</button>') +
        '<button type="button" class="pwa-btn-fermer" aria-label="Fermer">&times;</button>' +
      '</div>';

    document.body.appendChild(banniere);
    requestAnimationFrame(function () { banniere.classList.add('visible'); });

    banniere.querySelector('.pwa-btn-fermer').onclick = function () {
      localStorage.setItem('pharmax_install_refus', String(Date.now()));
      banniere.classList.remove('visible');
      setTimeout(function () { banniere.remove(); }, 300);
    };

    var boutonInstaller = banniere.querySelector('.pwa-btn-installer');
    if (boutonInstaller) {
      boutonInstaller.onclick = function () { lancerInstallation(); };
    }
    return banniere;
  }

  function lancerInstallation() {
    if (!invite) return false;
    invite.prompt();
    invite.userChoice.then(function (choix) {
      if (choix.outcome !== 'accepted') {
        localStorage.setItem('pharmax_install_refus', String(Date.now()));
      }
      invite = null;
      masquerBoutonPage();
      var b = document.querySelector('.pwa-banner');
      if (b) { b.classList.remove('visible'); setTimeout(function () { b.remove(); }, 300); }
    });
    return true;
  }

  // Bouton d'installation intégré à la page (volet de connexion)
  function boutonPage() { return document.getElementById('btnInstallerFlow'); }

  function afficherBoutonPage() {
    var bouton = boutonPage();
    if (!bouton || estInstallee()) return;
    bouton.hidden = false;
    bouton.onclick = function () {
      if (!lancerInstallation()) {
        // Aucune invite disponible : on explique la marche à suivre
        alert(estIOS()
          ? "Sur iPhone : appuyez sur le bouton Partager, puis « Sur l'écran d'accueil »."
          : "Votre navigateur ne propose pas l'installation ici. Vérifiez que le site est "
            + "servi en HTTPS, puis utilisez le menu du navigateur, option « Installer l'application ».");
      }
    };
  }

  function masquerBoutonPage() {
    var bouton = boutonPage();
    if (bouton) bouton.hidden = true;
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();          // on garde la main sur le moment d'affichage
    invite = e;
    afficherBoutonPage();        // le bouton de la page devient actif
    if (estInstallee() || banniereRefusee()) return;
    // Un court délai évite d'interrompre l'utilisateur dès l'ouverture
    setTimeout(function () { construireBanniere(false); }, 2500);
  });

  window.addEventListener('appinstalled', function () {
    localStorage.removeItem('pharmax_install_refus');
    masquerBoutonPage();
    var b = document.querySelector('.pwa-banner');
    if (b) b.remove();
  });

  function proposerSurIOS() {
    if (!estIOS() || estInstallee()) return;
    afficherBoutonPage();
    if (banniereRefusee()) return;
    setTimeout(function () { construireBanniere(true); }, 3000);
  }

  // ---------------------------------------------------------------------
  // 3. Défilement principal en volets pleine page
  //    Quatre volets de présentation, puis la connexion. Le geste tactile
  //    reste natif (scroll-snap) ; les commandes pilotent le même
  //    défilement, ce qui garantit que l'indicateur ne ment jamais.
  // ---------------------------------------------------------------------
  function gererFlow() {
    var flow = document.getElementById('appFlow');
    var commandes = document.getElementById('flowControls');
    var zonePoints = document.getElementById('flowDots');
    var suivant = document.getElementById('flowNext');
    var passer = document.getElementById('flowSkip');
    if (!flow || !commandes || !zonePoints) return;

    var volets = flow.querySelectorAll('.flow-panel');
    if (volets.length < 2) return;

    var indexConnexion = volets.length - 1;   // le dernier volet est la connexion
    var courant = 0;

    // Un point par volet de présentation : la connexion n'en a pas
    for (var i = 0; i < indexConnexion; i++) {
      (function (index) {
        var point = document.createElement('button');
        point.type = 'button';
        point.className = 'flow-dot' + (index === 0 ? ' actif' : '');
        point.setAttribute('aria-label', 'Étape ' + (index + 1) + ' sur ' + indexConnexion);
        point.onclick = function () { allerA(index); };
        zonePoints.appendChild(point);
      })(i);
    }

    var points = zonePoints.querySelectorAll('.flow-dot');

    function allerA(index, immediat) {
      flow.scrollTo({
        left: volets[index].offsetLeft - flow.offsetLeft,
        behavior: immediat ? 'auto' : 'smooth'
      });
    }

    function majCommandes(index) {
      courant = index;
      points.forEach(function (p, i) { p.classList.toggle('actif', i === index); });

      // Sur le volet de connexion, la barre s'efface pour libérer l'écran
      commandes.classList.toggle('masquee', index >= indexConnexion);
      if (index >= indexConnexion) {
        try { sessionStorage.setItem('pharmax_onboarding_vu', '1'); } catch (e) {}
      }

      if (!suivant) return;
      var dernierVolet = index === indexConnexion - 1;
      suivant.querySelector('span').textContent = dernierVolet ? 'Commencer' : 'Suivant';
      suivant.querySelector('i').style.display = dernierVolet ? 'none' : '';
    }

    var enCours = false;
    flow.addEventListener('scroll', function () {
      if (enCours) return;
      enCours = true;
      requestAnimationFrame(function () {
        var centre = flow.scrollLeft + flow.clientWidth / 2;
        var actif = 0;
        volets.forEach(function (volet, i) {
          var debut = volet.offsetLeft - flow.offsetLeft;
          if (centre >= debut && centre < debut + volet.offsetWidth) actif = i;
        });
        majCommandes(actif);
        enCours = false;
      });
    }, { passive: true });

    if (suivant) {
      suivant.onclick = function () { allerA(Math.min(courant + 1, indexConnexion)); };
    }
    if (passer) {
      passer.onclick = function () { allerA(indexConnexion); };
    }

    // Présentation déjà parcourue dans la session : on ouvre sur la connexion
    var dejaVu = false;
    try { dejaVu = !!sessionStorage.getItem('pharmax_onboarding_vu'); } catch (e) {}
    if (dejaVu && surMobile()) {
      allerA(indexConnexion, true);
      majCommandes(indexConnexion);
    }

    // Un formulaire soumis avec erreur doit ramener directement à la connexion
    if (document.querySelector('.flash-item') && surMobile()) {
      allerA(indexConnexion, true);
      majCommandes(indexConnexion);
    }
  }

  // ---------------------------------------------------------------------
  // 4. Enregistrement du service worker
  // ---------------------------------------------------------------------
  function enregistrerServiceWorker() {
    if (!window.isSecureContext) {
      console.warn('PharmaX : installation indisponible. Le site doit être servi en HTTPS '
        + '(ou depuis localhost). Sur une adresse http://192.168.x.x, les navigateurs '
        + "refusent le service worker et l'invite d'installation.");
      return;
    }
    if (!('serviceWorker' in navigator)) return;
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').catch(function () {
        /* échec silencieux : l'application fonctionne sans service worker */
      });
    });
  }

  // ---------------------------------------------------------------------
  function demarrer() {
    gererSplash();
    gererFlow();
    proposerSurIOS();
    enregistrerServiceWorker();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', demarrer);
  } else {
    demarrer();
  }
})();
