# kotlinx.serialization: keep generated serializers of the API DTOs.
-keepattributes *Annotation*, InnerClasses, Signature, Exceptions
-keepclassmembers @kotlinx.serialization.Serializable class co.soclose.sosforge.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class co.soclose.sosforge.**$$serializer { *; }
-dontwarn okhttp3.internal.platform.**
-dontwarn org.conscrypt.**
-dontwarn org.bouncycastle.**
-dontwarn org.openjsse.**
# MapLibre Native: JNI-bound classes must keep their names.
-keep class org.maplibre.android.** { *; }
-dontwarn org.maplibre.android.**
