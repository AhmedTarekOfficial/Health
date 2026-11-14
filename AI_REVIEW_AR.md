# مراجعة OrbitNexus — 14 نوفمبر 2026

تم استنساخ `https://github.com/youssefamr010/OrbitNexus` عند commit `a1f0c8f224a8397f122fc589ce59a4e52ed620f2` داخل فولدر مستقل. النسخة الكاملة موجودة في `OrbitNexus_SpaceApps_Project.zip`، وتم فكها داخل `extracted` مع إبقاء ملفاتها كما هي. تم التحقق من تطابق الـ51 ملفًا المستخرج مع الأرشيف. ملفات مشروع Health Monitoring الأساسي لم تُعدّل أثناء هذه المراجعة.

**النتيجة:** توجد واجهة مدمجة مع Flask وملفات خمسة نماذج ML واتصال بمزوّد LLM. لكن النسخة الحالية نموذج تجريبي فيه محاكاة ومشاكل ربط فعلية؛ الأدلة المرفقة لا تثبت جاهزيته لاتخاذ قرارات صحية حقيقية. هذا تقييم للكود المتاح، ولا يستنتج مستوى أو تخصص صاحبه.

## كيف يعمل فعليًا؟

1. Flask يعرض `templates/index.html`، والواجهة تستدعي API من ملفات JavaScript. الكتابة تتجه إلى `POST /api/chat`، والتعرف على الكلام في المتصفح يحوّل الصوت لنص ويرسله لنفس الشات.
2. `AstraIntelligenceEngine` يحمّل خمسة ملفات `.pkl` باستخدام `joblib` وينفذ التنبؤ على متجهات Features جاهزة. لا توجد في هذا المسار سلسلة مكتملة لاستخراج هذه المدخلات من موجات ساعة فعلية.
3. هناك مولّد بيانات باستخدام موجات رياضية وأرقام عشوائية. الـREST endpoint يولّد بيانات عند خلو الحالة، وهناك أيضًا حلقة محاكاة منفصلة تبدأ عند اتصال Socket.IO.
4. الشات يجمع رسالة المستخدم وسجل المحادثة والذاكرة و`telemetry_state`، ثم يضع السياق في رسائل الـLLM.
5. الاستدعاء الفعلي يتم عبر **Groq API**؛ اسم الموديل الافتراضي في الكود `llama-3.3-70b-versatile` ويمكن تغييره. يلزم إنترنت ومفتاح API، ولا توجد أوزان LLM محلية أو بديل محلي عند انقطاع الاتصال. اسم الموديل هنا وصف للإعداد المرفق، وليس تحققًا من توفره الآن لدى المزوّد.

إذًا فكرة «الـLLM يشرح تنبؤات ML» موجودة في الكود كإضافة التنبؤات إلى الـprompt، وليست تدريبًا مشتركًا أو fine-tuning. نجاح وصول البيانات إليه يعتمد على المسار الذي حدّث الحالة؛ وهذا فيه خلل موضح أدناه. `llm_assistant.py` يحتوي Wrapper آخر، لكن `app.py` لا يستخدمه في مسار الشات الفعلي. لم أجد RAG أو قاعدة بروتوكولات موثقة يسترجع منها الشات مراجع.

الأدلة: [إرسال الشات من الواجهة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/checkin/checkin.js:188>)، [تجميع سياق الشات](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:639>)، [اتصال Groq](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/provider_config.py:101>)، [تحميل النماذج](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/astra_engine.py:28>).

## النماذج وبيانات التدريب

الجدول يصف كود التدريب والـmetadata، وليس نتيجة تشغيل ملفات PKL أو إثباتًا لمصدر تدريبها:

| الجزء | الخوارزمية المذكورة في الكود/metadata | ما يفعله Notebook المرفق |
| --- | --- | --- |
| Activity | RandomForestClassifier، 22 feature | يقرأ PAMAP2 parquet إن وجد؛ وإلا يولّد features وlabels عشوائية مستقلة |
| Stress | XGBoost، 5 features | يولّد توزيعات صناعية للنبض وHRV وEDA وغيرها؛ لا يقرأ WESAD فعليًا في هذه الخلية |
| Heart rate | ExtraTreesRegressor | يصنع features صناعية ويحسب الهدف من معادلة؛ يدرب على 6 features |
| Anomaly | IsolationForest، 5 features | يدرب على توزيعات صناعية لقياسات مفترضة طبيعية |
| Space biology | RandomForestClassifier، 50 feature | يصنع مجموعتين عشوائيتين بمتوسطين مختلفين، ثم يصنفهما Ground/Flight |

**لا توجد ملفات datasets فعلية في الأرشيف.** هناك أسماء فولدرات فقط. الأرقام مثل 99.62% و100% موجودة في metadata/الواجهة، لكن لم تتوفر البيانات أو سجلات تحقق كافية لإعادة إنتاجها أو إثبات صلاحيتها على رواد فضاء. Notebook الـactivity يسمح ببيانات حقيقية غير مرفقة؛ لذلك لا يصح الجزم بأن كل ملفات PKL تدربت على بيانات صناعية. كما أن اختلاف Notebook النبض عن metadata يؤكد أن تطابق وصفة التدريب مع الملفات المرفقة غير مضمون.

الأدلة: [Activity ومسار الـfallback](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:121>)، [Stress الصناعي](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:167>)، [النبض الصناعي](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:231>)، [Anomaly](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:281>)، [Space biology](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:323>).

## المشاكل ذات الأولوية

**1. بيانات الشاشة لا تصل للشات في مسار الواجهة المعتاد — أولوية عالية.**

الواجهة تطلب `/api/telemetry/live` كل ثانيتين. عند بدء السيرفر، هذا المسار يولّد قراءات وتنبؤات في متغير محلي ولا يحفظها في `telemetry_state`. الشات يقرأ `telemetry_state` فقط. الواجهة المرفقة لا تنشئ اتصال Socket.IO لتشغيل الحلقة التي تحدث الحالة. النتيجة عند بدء جديد واستخدام الواجهة وحدها: بيانات ظاهرة، لكن الشات لا يستقبلها. أكّد اختبار معزول أن قراءة نبض 123 ظهرت في نتيجة live، ثم بقيت الحالة فارغة والـprompt بلا telemetry. مولّد البيانات والمزوّد استُبدلا بـstubs لاختبار نقل الحالة، وليس لاختبار النماذج.

أيضًا `POST /api/telemetry` يحفظ التنبؤات دون القياسات الخام، والتحديث يستخدم `update` بلا تحقق من عمر كل قراءة. المطلوب Snapshot موحد به المصدر والوقت والقياسات والتنبؤات، تستخدمه الشاشة والشات معًا.

الأدلة: [REST live](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:391>)، [قراءة الشات للحالة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:647>)، [Polling الواجهة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/dashboard/dashboard.js:303>)، [حفظ POST telemetry](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:616>).

**2. “Real-Time / BLE connected” يعرض محاكاة — أولوية عالية.**

حالة الساعة تبدأ `connected=True` مع بيانات ثابتة. زر الاتصال يغير Boolean؛ تعريفات الـadapters تقوم بتوحيد Payloads ولا تنفّذ اتصالًا فعليًا بالأجهزة. `_generate_telemetry` يولد الأرقام عشوائيًا، بما فيها 50 قيمة جينية placeholder. تغيير Demo/Realtime في الواجهة محلي ولا يستدعي `/api/mode`. لا يوجد دليل في المسار المرفق على قراءة BLE حقيقية رغم تسميات الواجهة. المطلوب إظهار Simulation بوضوح وربط driver فعلي قبل إظهار Connected/Live.

الأدلة: [حالة الساعة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:115>)، [مولّد البيانات](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:887>)، [تغيير وضع الواجهة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/dashboard/dashboard.js:267>).

**3. غياب البيانات أو النماذج قد يعطي حالة صحية مطمئنة — أولوية عالية.**

`process_astronaut_telemetry({})` يرجع `ALL SYSTEMS NOMINAL (Astronaut EVA/IVA Ready)`. ونفس الملخص يظهر إذا رجع نموذجا Stress وAnomaly أخطاء عدم التحميل؛ الافتراضي يعتبر عدم وجود alert علامة سلامة. تم إثبات الحالتين باختبار معزول. المطلوب حالات منفصلة مثل Unknown / Insufficient data / Model unavailable، مع فحص schema والمدخلات والحداثة.

الدليل: [حساب الملخص](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/astra_engine.py:120>).

**4. الرد الصوتي قد يناقض الرد المكتوب — أولوية عالية، خاصة للواجهة الصوتية.**

`VoiceAssistant` يستبدل النص بجمل ثابتة حسب كلمات مفتاحية. الاختبار: النص `No signs of stress were detected.` ينتج صوتًا يبدأ `Stress appears elevated.`. جمل أخرى عن الأكسجين مثلًا قد تختزل إلى جملة عامة عن استمرار المراقبة. المتصفح ينطق `voice.text` بالفعل. المطلوب نطق الرد الصحيح أو ملخص يحافظ على المعنى مع اختبارات للنفي والتحذيرات؛ السلوك الحالي لا يصلح للاعتماد على الصوت وحده.

الأدلة: [توليد النص الصوتي](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/voice_assistant.py:12>)، [تشغيله في المتصفح](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/checkin/checkin.js:209>).

**5. درجات “الأمراض” قواعد ثابتة وليست احتمالات متحققة من ML — أولوية عالية.**

`evaluate_disease_risks` يرجع ست بطاقات من شروط وحدود ثابتة. مثال: Radiation score يظل 18 حتى عند تغيير genomics؛ وعبارة 100% Adaptation Index مكتوبة حرفيًا. حالة ضغط البدلة أيضًا ثابتة ولا تتلقى ضغط بدلة حقيقيًا. وجود نموذج Space biology لا يجعل هذه البطاقة مستندة إليه. المطلوب تمييز القواعد عن تنبؤات النماذج وإزالة ادعاءات التحقق/الاعتماد غير المدعومة، وإرفاق مصدر وتقييم لكل بروتوكول قبل استخدامه.

الدليل: [قواعد المخاطر والعبارات الثابتة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:138>).

**6. التوثيق والتدريب غير متسقين مع التشغيل — أولوية عالية.**

Notebook النبض يدرب على 6 features، والـmetadata المرفق والمولّد الحالي يستخدمان 9. إعادة التدريب بالـNotebook ثم استبدال الملف ستجعل متجه التشغيل غير متوافق مع النموذج المعاد تدريبه. هذا لا يثبت أن ملف PKL المرفق يفشل حاليًا؛ لم يتم تحميله. كذلك وصف SVM/RandomForest/Kalman في أجزاء من التوثيق لا يطابق ExtraTrees/RandomForest في كود التدريب والـmetadata. المطلوب وصفة تدريب قابلة للإعادة، schema موحد، بيانات وتوثيق تقسيمات التقييم، وإصدارات مكتبات مثبتة.

الأدلة: [6 features في التدريب](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/notebooks/ASTRA_AI_Training.ipynb:244>)، [9 في metadata](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/models/heart_model/heart_meta.json:5>)، [9 في التشغيل](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:935>).

**7. حماية الـAPI وعزل المستخدمين غير مكتملين — أولوية عالية عند مشاركة السيرفر.**

الحارس يسمح بالطلب إذا لم يوجد Token أصلًا؛ وجود Token غير صحيح فقط هو الذي يؤدي للرفض. تأكد ذلك باختبار POST مجهول إلى مسار الشات على مستوى الحارس. `chat_history` و`memory_manager` و`telemetry_state` مشتركة بين كل العملاء؛ يمكن أن يدخل سياق شخص في رد لآخر. Endpoint الذاكرة public، وCORS يسمح بكل origins، والسيرفر يستمع على `0.0.0.0`. دالة rate limit موجودة لكن غير مستدعاة من الحارس. المطلوب فرض مصادقة وصلاحيات وعزل الحالة لكل مستخدم/رائد، وتفعيل الحدود فعليًا.

الأدلة: [السماح دون Token](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:470>)، [الحالة المشتركة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:50>)، [نشر السيرفر](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:1058>).

**8. إدخال HTML من رسائل الشات دون تعقيم — أولوية عالية عند إدخال محتوى غير موثوق.**

رسالة المستخدم ورد الموديل يدخلان `innerHTML`. دالة `formatMarkdown` تستبدل الأسطر والنجوم فقط، ولا تهرّب HTML أو تعقمه؛ بالتالي يوجد مسار DOM XSS، بما فيه رد مولّد يحتوي HTML. هذه نتيجة مراجعة المصدر؛ لم أنفّذ payload في متصفح. المطلوب `textContent` أو Markdown renderer مع sanitizer مناسب.

الدليل: [بناء فقاعات الشات وformatMarkdown](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/checkin/checkin.js:230>).

## مشاكل إضافية

- **Online learning منفصل عن النماذج الخمسة:** يتم تدريب `SGDClassifier` آخر، ولا يستخدمه مسار `AstraIntelligenceEngine`. حساب الدقة يتم على العينة نفسها بعد تدريب النموذج عليها؛ فلا يمثل تقييمًا مستقلًا. زر الواجهة يرسل عينة ثابتة بنفس label. [الخادم](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:693>)، [زر الواجهة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/dashboard/dashboard.js:408>).
- **تنبيهات قديمة:** في حلقة المحاكاة يتم تحديث `active_alerts` عند وجود تنبيهات فقط، ولا تمسح عند عودة النتائج الطبيعية. الشات قد يحتفظ بتنبيه سابق. [المصدر](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:1028>).
- **تكرار route:** `/api/sensors/overview` معرف مرتين بمخرجات مختلفة؛ التسجيل الأول يمكن أن يحجب الثاني. كشف الـAST التعريفين عند الدالتين في السطرين 541 و774. [الأول](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:540>)، [الثاني](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/app.py:773>).
- **ذاكرة غير دائمة:** القائمة داخل RAM فقط رغم وصفها persistent، وتختفي بعد إعادة التشغيل. نص المستخدم في ملخص الذاكرة يعاد وضعه داخل system prompt أيضًا، دون فصل واضح بين بيانات المستخدم والتعليمات. [الذاكرة](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/memory_manager.py:8>).
- **عدم تطابق التفاصيل مع الكروت:** تحديث live يغير DOM لكنه لا يحدث `currentVitals/currentModels/currentDiseases` التي تستخدمها النوافذ التفصيلية؛ يمكن أن تعرض النافذة قيم Demo بعد تغير الكارت. [التحديث](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/extracted/ASTRA/website/src/modules/dashboard/dashboard.js:303>).

## ما تم اختباره وحدود النتيجة

- نجحت اختبارات syntax لكل ملفات Python الـ15 دون استيراد التطبيق.
- نجحت 7 اختبارات أصلية مستقلة عن Flask والنماذج: الذاكرة، الصوت، تطبيع الحساسات ومحاكاتها، Token manager، orchestrator، وإعدادات المزوّد. معظمها يفحص شكل النتائج/سلوكًا محدودًا؛ نجاحها لا يثبت صحة الاستنتاجات الصحية أو ربط النظام كاملًا.
- نجحت فحوص معزولة مضافة لإعادة إنتاج: الحالة الطبيعية عند غياب البيانات/النماذج، انفصال live عن سياق الشات، قبول الطلب المجهول، تناقض الصوت، ثبات درجة الإشعاع، تكرار route، واختلاف عدد features. يمكن تشغيل [review_checks.py](</home/ahmed/Documents/ChatGPT/Health Monitoring system/OrbitNexus/review_checks.py>) باستخدام Python القياسي. الفحوص تستخرج الدوال من AST وتستخدم stubs واضحة؛ ليست اختبارات HTTP أو موديلات فعلية.
- لم أشغّل Flask، ولم أحمّل ملفات PKL، ولم أتصل بـGroq أو بساعة حقيقية. مكتبات التشغيل المطلوبة غير مثبتة في البيئة المتاحة، ولا يوجد Node لإجراء فحص JavaScript آلي. لم أثبت جودة أو دقة النماذج الثنائية، ولم أجر اختبار متصفح شاملًا.
- لم أعدل كود المشروع المستخرج أو أدمجه في مشروعك الأساسي. الإضافات هي تقرير المراجعة وسكربت الفحص فقط داخل فولدر الاستنساخ.

الأولوية العملية قبل الدمج: توحيد مصدر بيانات الشاشة والشات، إظهار المحاكاة والبيانات المفقودة بصدق، تصحيح الصوت، ثم التحقق من بيانات التدريب ومدخلات النماذج، وإغلاق مشاكل المصادقة والعزل وHTML. اطلب من الفريق ملفات/مصادر البيانات، وصفة التدريب المطابقة للـPKL، تقسيم التقييم على أشخاص/جلسات منفصلة، وسجل تجربة متكاملة بقراءة حقيقية وصلت إلى prompt ثم رد الشات. هذه أدلة قابلة للفحص على مستوى النظام الفعلي.
