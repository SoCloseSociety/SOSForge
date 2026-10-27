package co.soclose.sosforge.screens

import androidx.activity.ComponentActivity
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/**
 * The overflow walker checked against cases with a known answer, so a green screenshot pass means
 * something: its first version flagged every short text (288 false failures), and a word broken
 * across lines ("Liv / e") went through unseen.
 */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(qualifiers = "w360dp-h780dp-xxhdpi")
class OverflowSelfTest {
    @get:Rule val rule = createAndroidComposeRule<ComponentActivity>()

    @Before fun only() = assumeTrue("screenshots run with -Psos.screens=true", System.getProperty("sos.screens") == "true")

    @Test fun a_short_text_is_fine_and_a_word_split_in_a_narrow_box_is_caught() {
        rule.setContent {
            Column {
                Text("Back")
                Box(Modifier.width(40.dp)) { Text("Live", fontSize = 40.sp) }
                Box(Modifier.width(60.dp)) { Text("a1b2c3d4e5f6a7b8c9d0e1f2", fontSize = 14.sp) }
            }
        }
        rule.waitForIdle()
        val root = rule.onRoot(useUnmergedTree = true).fetchSemanticsNode()
        val (hard, _) = Overflow.check(root, rule.activity.window.decorView.width)
        assertEquals("only the split word: $hard", 1, hard.size)
        assertTrue(hard.single(), hard.single().startsWith("Live (word broken across lines"))
    }
}
