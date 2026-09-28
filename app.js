const intro = document.getElementById('intro');
const site = document.getElementById('site');
const enterBtn = document.getElementById('enterBtn');
const introVideo = document.getElementById('introVideo');
const skipIntro = document.getElementById('skipIntro');
const modal = document.getElementById('modal');
const modalVideo = document.getElementById('modalVideo');
const closeModal = document.getElementById('closeModal');
const VIDEO_BG = 'assets/video/bachata-bg.mp4';
const VIDEO_EN = 'assets/video/bachata.mp4';

function currentLanguage(){
  return document.documentElement.lang === 'en' ? 'en' : 'bg';
}
function storyVideoForLanguage(){
  return currentLanguage() === 'en' ? VIDEO_EN : VIDEO_BG;
}
function setVideoFile(videoEl, src){
  videoEl.pause();
  videoEl.removeAttribute('src');
  while(videoEl.firstChild) videoEl.removeChild(videoEl.firstChild);
  videoEl.load();
  videoEl.src = src;
  videoEl.load();
}


document.body.classList.add('intro-open');

function revealSite(){
  intro.style.transition = 'opacity .85s ease';
  intro.style.opacity = '0';
  setTimeout(()=>{
    intro.classList.add('hidden');
    site.classList.remove('hidden');
    document.body.classList.remove('intro-open');
    window.scrollTo(0,0);
  },850);
}

function showSiteImmediately(){
  introVideo.pause();
  intro.classList.add('hidden');
  site.classList.remove('hidden');
  document.body.classList.remove('intro-open');
  window.scrollTo(0,0);
}

let openingFromIntro = false;

function playOpening(){
  // v9: do NOT change the page layout before video.play().
  // Keep the intro mounted behind the modal and use the exact same modal player.
  openingFromIntro = true;
  openTrailer('story');
}

function finishModal(){
  modalVideo.pause();
  modal.classList.add('hidden');
  if(openingFromIntro){
    openingFromIntro = false;
    showSiteImmediately();
  }
}
enterBtn.addEventListener('click', playOpening);
skipIntro.addEventListener('click', showSiteImmediately);

function openTrailer(src='story', isFirstDance=false){
  // "Play our story" and "First Dance" both use the selected language's
  // single rendered video. No extra HTML captions or subtitle tracks.
  let selectedSrc = src;
  if(src === 'story' || isFirstDance || src.includes('bachata') || src.includes('trailer')){
    selectedSrc = storyVideoForLanguage();
  }

  modal.classList.remove('hidden');
  setVideoFile(modalVideo, selectedSrc);
  modalVideo.currentTime = 0;
  modalVideo.play().catch(()=>{});
}
document.getElementById('trailerBtn').addEventListener('click', ()=>openTrailer('story'));
document.querySelectorAll('[data-video]').forEach(el=>el.addEventListener('click',()=>openTrailer(el.dataset.video, el.querySelector('[data-i18n="episode1_title"]') !== null)));
closeModal.addEventListener('click', finishModal);
modal.addEventListener('click', e=>{ if(e.target===modal) finishModal(); });
modalVideo.addEventListener('ended', finishModal);

const countdown = document.querySelector('.countdown');
const target = new Date(countdown.dataset.date).getTime();
function tick(){
  const diff = Math.max(0,target-Date.now());
  const d = Math.floor(diff/86400000);
  const h = Math.floor((diff%86400000)/3600000);
  const m = Math.floor((diff%3600000)/60000);
  const s = Math.floor((diff%60000)/1000);
  document.getElementById('days').textContent = String(d).padStart(3,'0');
  document.getElementById('hours').textContent = String(h).padStart(2,'0');
  document.getElementById('minutes').textContent = String(m).padStart(2,'0');
  document.getElementById('seconds').textContent = String(s).padStart(2,'0');
}
tick(); setInterval(tick,1000);


const translations = {
  bg: {
    name_milena:"МИЛЕНА", name_krasi:"КРАСИ",
    play_story:"▶ PLAY",
    skip_intro:"ПРОПУСНИ ИНТРОТО →",
    intro_tagline:"Нашата история започна с един танц.",
    nav_story:"История", nav_episodes:"Епизоди", nav_details:"Детайли", nav_rsvp:"RSVP",
    hero_kicker:"2027 · Романтика · Приключения · Един живот заедно",
    hero_copy:"Някои истории започват със „Здравей“. Нашата започна с един танц.",
    hero_invite:"Следващият епизод от нашата история започва на 4 септември 2027. Ще се радваме да го споделим с вас.",
    watch_again:"↻ Гледай трейлъра отново", more_info:"ⓘ Повече информация",
    story_title:"От танца до завинаги",
    story1_title:"Как започна всичко", story1_text:"Един танц. Един ритъм. Началото на любимата ни история.",
    story2_title:"Приключения заедно", story2_text:"Градове, залези, плажове и хиляди спомени между тях.",
    story3_title:"От танца до завинаги", story3_text:"На 04.09.2027 започва следващия епизод.",
    episodes_title:"Епизоди от нашата история", season:"Сезон 1",
    episode1_label:"Епизод 1", episode1_title:"Първият танц", episode1_text:"Тук започна всичко.",
    episode2_label:"Епизод 2", episode2_title:"Влюбването", episode2_text:"Моменти, пътувания, море.",
    episode3_label:"Епизод 3", episode3_title:"Сватбата", episode3_text:"Премиера 04.09.2027",
    quote:"“Най-хубавият танц е този, който продължава през целия живот.”",
    quote_names:"— Милена & Краси",
    next_chapter:"СЛЕДВАЩИЯТ ЕПИЗОД", chapter_line:"Там, където започва следващото ни приключение.",
    location:"Локация", location_small:"Черноморско крайбрежие, България",
    ceremony:"Церемония", ceremony_time:"16:00 ч.", ceremony_small:"Моля, бъдете на място малко по-рано.",
    mood:"Настроение", mood_value:"Красив морски залез", mood_small:"Море, музика и танци под звездите.",
    days:"дни", hours:"часа", minutes:"минути", seconds:"секунди",
    stay_title:"Настаняване", stay_intro:"Ако искате да останете близо до нас, ето няколко удобни варианта.", stay_tc:"На мястото на сватбата", stay_tc_small:"10% отстъпка при предплащане.", stay_alt:"Съвсем близо до Thracian Cliffs",
    rsvp_eyebrow:"ЩЕ БЪДЕТЕ ЛИ С НАС?", rsvp_title:"Запазете един танц за нас.",
    rsvp_text:"Ще се радваме да споделите този ден с нас. Моля, потвърдете до 31.01.2027.",
    name_placeholder:"Вашето име / семейство", adults_placeholder:"Брой възрастни", children_placeholder:"Брой деца", dietary_placeholder:"Хранителни изисквания / алергии", message_placeholder:"Съобщение към Милена & Краси (по желание)", attend_question:"Ще присъствате ли?",
    attend_yes:"Да, с удоволствие ❤️", attend_no:"За съжаление, не",
    send_rsvp:"Изпрати RSVP", sending_rsvp:"Изпращане…", rsvp_error:"Възникна проблем. Моля, опитайте отново.", thanks:"Получено. До скоро на дансинга! 💃🕺",
    footer_line:"Някои истории свършват. Нашата тепърва започва."
  },
  en: {
    name_milena:"MILENA", name_krasi:"KRASI",
    play_story:"▶ PLAY OUR STORY",
    skip_intro:"SKIP INTRO →",
    intro_tagline:"Our story started with a dance.",
    nav_story:"Story", nav_episodes:"Episodes", nav_details:"Details", nav_rsvp:"RSVP",
    hero_kicker:"2027 · Romance · Adventure · A lifetime together",
    hero_copy:"Some stories begin with a hello. Ours began with a dance.",
    hero_invite:"The next chapter of our story begins on September 4, 2027. We’d love to share it with you.",
    watch_again:"↻ Watch the trailer again", more_info:"ⓘ More information",
    story_title:"From Dancing to Forever",
    story1_title:"How It Started", story1_text:"One dance. One rhythm. The beginning of our favorite story.",
    story2_title:"Adventures Together", story2_text:"Cities, sunsets, beaches and a thousand memories in between.",
    story3_title:"From Dancing to Forever", story3_text:"On 04.09.2027, the next chapter begins.",
    episodes_title:"Episodes from our story", season:"Season 1",
    episode1_label:"Episode 1", episode1_title:"The First Dance", episode1_text:"This is where it all began.",
    episode2_label:"Episode 2", episode2_title:"Falling in Love", episode2_text:"Moments, journeys and the sea.",
    episode3_label:"Episode 3", episode3_title:"The Wedding", episode3_text:"Coming 04.09.2027",
    quote:"“The best dance is the one that lasts a lifetime.”",
    quote_names:"— Milena & Krasi",
    next_chapter:"THE NEXT CHAPTER", chapter_line:"Where our next adventure begins.",
    location:"Location", location_small:"Black Sea Coast, Bulgaria",
    ceremony:"Ceremony", ceremony_time:"4:00 PM", ceremony_small:"Please arrive a little earlier.",
    stay_title:"Accommodation", stay_intro:"If you’d like to stay close to us, here are a few convenient options.", stay_tc:"At the wedding venue", stay_tc_small:"10% discount with prepayment.", stay_alt:"Just a short distance from Thracian Cliffs",
    mood:"Mood", mood_value:"Beautiful seaside sunset", mood_small:"Sea, music and dancing under the stars.",
    days:"days", hours:"hours", minutes:"minutes", seconds:"seconds",
    rsvp_eyebrow:"ARE YOU WATCHING WITH US?", rsvp_title:"Save one dance for us.",
    rsvp_text:"We would love to share this day with you. Please RSVP by 31 January 2027.",
    name_placeholder:"Your name / family", adults_placeholder:"Number of adults", children_placeholder:"Number of children", dietary_placeholder:"Dietary requirements / allergies", message_placeholder:"Message for Milena & Krasi (optional)", attend_question:"Will you attend?",
    attend_yes:"Yes, with pleasure ❤️", attend_no:"Unfortunately, no",
    send_rsvp:"Send RSVP", sending_rsvp:"Sending…", rsvp_error:"Something went wrong. Please try again.", thanks:"Received. See you on the dance floor! 💃🕺",
    footer_line:"Some stories end. Ours is just beginning."
  }
};

function setLanguage(lang){
  document.documentElement.lang = lang;
  localStorage.setItem('weddingLang', lang);
  document.querySelectorAll('[data-i18n]').forEach(el=>{
    const key=el.dataset.i18n;
    if(translations[lang][key]!==undefined){
      el.textContent=translations[lang][key];
    }
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el=>{
    const key=el.dataset.i18nPlaceholder;
    if(translations[lang][key]!==undefined) el.placeholder=translations[lang][key];
  });
  document.querySelectorAll('.lang-btn').forEach(btn=>btn.classList.toggle('active',btn.dataset.lang===lang));
  if(!intro.classList.contains('playing')){
    setVideoFile(introVideo, storyVideoForLanguage());
  }
}
document.querySelectorAll('.lang-btn').forEach(btn=>btn.addEventListener('click',()=>setLanguage(btn.dataset.lang)));
setLanguage(localStorage.getItem('weddingLang') || 'bg');

const RSVP_ENDPOINT='https://script.google.com/macros/s/AKfycbyNgDhbJJl4aMWtH2IdFNLYxVSaGefbmEOI_4bhIKZXnXg6w_5XgVrr95peRq00wZyoTg/exec';
const rsvpForm=document.getElementById('rsvpForm'),rsvpAttendance=document.getElementById('rsvpAttendance'),guestDetails=document.getElementById('guestDetails'),rsvpAdults=document.getElementById('rsvpAdults'),rsvpSubmit=document.getElementById('rsvpSubmit'),rsvpError=document.getElementById('rsvpError');
function updateGuestFields(){const yes=rsvpAttendance.value==='Да';guestDetails.classList.toggle('hidden',!yes);rsvpAdults.required=yes;if(!yes){rsvpAdults.value='';document.getElementById('rsvpChildren').value='';document.getElementById('rsvpDietary').value='';}}
rsvpAttendance.addEventListener('change',updateGuestFields);
rsvpForm.addEventListener('submit',async e=>{e.preventDefault();rsvpError.classList.add('hidden');const lang=document.documentElement.lang==='en'?'en':'bg',t=translations[lang];rsvpSubmit.disabled=true;rsvpSubmit.textContent=t.sending_rsvp;const payload={name:document.getElementById('rsvpName').value.trim(),attendance:rsvpAttendance.value,adults:document.getElementById('rsvpAdults').value,children:document.getElementById('rsvpChildren').value,dietary:document.getElementById('rsvpDietary').value.trim(),message:document.getElementById('rsvpMessage').value.trim()};try{await fetch(RSVP_ENDPOINT,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain;charset=utf-8'},body:JSON.stringify(payload)});rsvpForm.classList.add('hidden');document.getElementById('thanks').classList.remove('hidden');}catch(err){rsvpError.classList.remove('hidden');rsvpSubmit.disabled=false;rsvpSubmit.textContent=t.send_rsvp;}});

const topbar=document.getElementById('topbar');
function onScroll(){topbar.classList.toggle('scrolled',window.scrollY>40);}
window.addEventListener('scroll',onScroll,{passive:true});onScroll();
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!modal.classList.contains('hidden'))finishModal();});

// Stretch the poster names to the full width of the page, whatever the language or font.
const heroNames=document.querySelector('.hero-names');
function fitNames(){
  if(!heroNames||site.classList.contains('hidden')) return;
  if(window.innerWidth<=640){heroNames.style.fontSize='';return;}
  const cs=getComputedStyle(heroNames);
  const avail=heroNames.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight);
  heroNames.style.fontSize='100px';
  const gap=avail*0.035;
  const parts=[...heroNames.children].reduce((w,el)=>w+el.getBoundingClientRect().width,0);
  const size=100*(avail-gap*2)/parts;
  heroNames.style.fontSize=Math.min(size,avail*0.2)+'px';
}
window.addEventListener('resize',fitNames);
if(document.fonts) document.fonts.ready.then(fitNames);
document.querySelectorAll('.lang-btn').forEach(btn=>btn.addEventListener('click',()=>requestAnimationFrame(fitNames)));
new MutationObserver(fitNames).observe(site,{attributes:true,attributeFilter:['class']});
