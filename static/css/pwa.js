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

    var dejaVu = sessionStorage.getItem('pharmax_splash_vu');
    if (dejaVu) {
      splash.remove();
      return;
    }

    document.body.classList.add('splash-actif');
    sessionStorage.setItem('pharmax_splash_vu', '1');

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
      boutonInstaller.onclick = function () {
        if (!invite) return;
        invite.prompt();
        invite.userChoice.then(function (choix) {
          if (choix.outcome !== 'accepted') {
            localStorage.setItem('pharmax_install_refus', String(Date.now()));
          }
          invite = null;
          banniere.classList.remove('visible');
          setTimeout(function () { banniere.remove(); }, 300);
        });
      };
    }
    return banniere;
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();          // on garde la main sur le moment d'affichage
    invite = e;
    if (estInstallee() || banniereRefusee()) return;
    // Un court délai évite d'interrompre l'utilisateur dès l'ouverture
    setTimeout(function () { construireBanniere(false); }, 2500);
  });

  window.addEventListener('appinstalled', function () {
    localStorage.removeItem('pharmax_install_refus');
    var b = document.querySelector('.pwa-banner');
    if (b) b.remove();
  });

  function proposerSurIOS() {
    if (!estIOS() || estInstallee() || banniereRefusee()) return;
    setTimeout(function () { construireBanniere(true); }, 3000);
  }

  // ---------------------------------------------------------------------
  // 3. Volets d'introduction
  //    Défilement tactile natif (scroll-snap) : le geste reste celui attendu
  //    sur mobile. Les commandes (points, bouton) pilotent le même
  //    défilement, de sorte que les deux modes d'usage restent synchronisés.
  // ---------------------------------------------------------------------
  function gererOnboarding() {
    var bloc = document.getElementById('onboarding');
    var piste = document.getElementById('onboardingTrack');
    var zonePoints = document.getElementById('onboardingDots');
    var suivant = document.getElementById('onboardingNext');
    var passer = document.getElementById('onboardingSkip');
    if (!bloc || !piste || !zonePoints) return;

    var volets = piste.querySelectorAll('.onboarding-slide');
    if (!volets.length) return;

    var courant = 0;

    volets.forEach(function (_, index) {
      var point = document.createElement('button');
      point.type = 'button';
      point.className = 'onboarding-dot' + (index === 0 ? ' actif' : '');
      point.setAttribute('aria-label', 'Volet ' + (index + 1) + ' sur ' + volets.length);
      point.onclick = function () { allerA(index); };
      zonePoints.appendChild(point);
    });

    var points = zonePoints.querySelectorAll('.onboarding-dot');

    function allerA(index) {
      piste.scrollTo({
        left: volets[index].offsetLeft - piste.offsetLeft,
        behavior: 'smooth'
      });
    }

    function majCommandes(index) {
      courant = index;
      points.forEach(function (p, i) { p.classList.toggle('actif', i === index); });
      if (!suivant) return;
      var dernier = index === volets.length - 1;
      suivant.querySelector('span').textContent = dernier ? 'Commencer' : 'Suivant';
      suivant.querySelector('i').style.display = dernier ? 'none' : '';
    }

    var enCours = false;
    piste.addEventListener('scroll', function () {
      if (enCours) return;
      enCours = true;
      requestAnimationFrame(function () {
        var centre = piste.scrollLeft + piste.clientWidth / 2;
        var actif = 0;
        volets.forEach(function (volet, i) {
          var debut = volet.offsetLeft - piste.offsetLeft;
          if (centre >= debut && centre < debut + volet.offsetWidth) actif = i;
        });
        majCommandes(actif);
        enCours = false;
      });
    }, { passive: true });

    function terminer() {
      bloc.classList.add('termine');
      try { sessionStorage.setItem('pharmax_onboarding_vu', '1'); } catch (e) {}
      var carte = document.querySelector('.auth-card');
      if (carte) carte.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }

    if (suivant) {
      suivant.onclick = function () {
        if (courant < volets.length - 1) allerA(courant + 1);
        else terminer();
      };
    }
    if (passer) passer.onclick = terminer;

    // Une fois parcourus dans la session, les volets ne réapparaissent pas
    try {
      if (surMobile() && sessionStorage.getItem('pharmax_onboarding_vu')) {
        bloc.classList.add('termine');
      }
    } catch (e) {}
  }

  // ---------------------------------------------------------------------
  // 4. Enregistrement du service worker
  // ---------------------------------------------------------------------
  function enregistrerServiceWorker() {
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
    gererOnboarding();
    proposerSurIOS();
    enregistrerServiceWorker();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', demarrer);
  } else {
    demarrer();
  }
})();
