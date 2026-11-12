package user_pool

import (
	"context"
	"strings"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/cognitoidentityprovider"
)

// systemTagKeyPrefixes are the key prefixes of tags injected by the ACK runtime
// or by AWS, which a user's Spec never carries.
var systemTagKeyPrefixes = []string{"services.k8s.aws/", "aws:"}

// customPreCompare compares Spec.UserPoolTags ignoring system-injected keys,
// which DescribeUserPool returns but only Spec.Tags is wired to manage.
func customPreCompare(delta *ackcompare.Delta, a, b *resource) {
	if a == nil || a.ko == nil || b == nil || b.ko == nil {
		return
	}
	desired := withoutSystemTags(a.ko.Spec.UserPoolTags)
	latest := withoutSystemTags(b.ko.Spec.UserPoolTags)
	if !ackcompare.MapStringStringPEqual(desired, latest) {
		delta.Add("Spec.UserPoolTags", a.ko.Spec.UserPoolTags, b.ko.Spec.UserPoolTags)
	}
}

// withoutSystemTags copies tags minus system-injected keys; the supplied map is
// never modified because a and b are the base of the runtime's merge patch.
func withoutSystemTags(tags map[string]*string) map[string]*string {
	filtered := make(map[string]*string, len(tags))
	for k, v := range tags {
		if isSystemTagKey(k) {
			continue
		}
		filtered[k] = v
	}
	return filtered
}

func isSystemTagKey(key string) bool {
	for _, prefix := range systemTagKeyPrefixes {
		if strings.HasPrefix(key, prefix) {
			return true
		}
	}
	return false
}

// syncTags examines the Tags in the supplied Resource and calls the
// TagResource and UntagResource APIs to ensure that the set of
// associated Tags stays in sync with the Resource.Spec.Tags
func (rm *resourceManager) SyncTags(
	ctx context.Context,
	resourceARN string,
	desiredTags map[string]*string,
	existingTags map[string]*string,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.syncTags")
	defer func() { exit(err) }()

	toAdd := map[string]string{}
	toDelete := []string{}

	for k, v := range desiredTags {
		if ev, found := existingTags[k]; !found || *ev != *v {
			toAdd[k] = *v
		}
	}

	for k, _ := range existingTags {
		if _, found := desiredTags[k]; !found {
			deleteKey := k
			toDelete = append(toDelete, deleteKey)
		}
	}

	if len(toAdd) > 0 {
		for k, v := range toAdd {
			rlog.Debug("adding tag to resource", "key", k, "value", v)
		}
		if err = rm.addTags(
			ctx,
			resourceARN,
			toAdd,
		); err != nil {
			return err
		}
	}
	if len(toDelete) > 0 {
		for _, k := range toDelete {
			rlog.Debug("removing tag from resource", "key", k)
		}
		if err = rm.removeTags(
			ctx,
			resourceARN,
			toDelete,
		); err != nil {
			return err
		}
	}

	return nil
}

// addTags adds the supplied Tags to the supplied resource
func (rm *resourceManager) addTags(
	ctx context.Context,
	resourceARN string,
	tags map[string]string,
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.addTag")
	defer func() { exit(err) }()

	input := &svcsdk.TagResourceInput{
		ResourceArn: &resourceARN,
		Tags:        tags,
	}

	_, err = rm.sdkapi.TagResource(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "TagResource", err)
	return err
}

// removeTags removes the supplied Tags from the supplied resource
func (rm *resourceManager) removeTags(
	ctx context.Context,
	resourceARN string,
	tagKeys []string, // the set of tag keys to delete
) (err error) {
	rlog := ackrtlog.FromContext(ctx)
	exit := rlog.Trace("rm.removeTag")
	defer func() { exit(err) }()

	input := &svcsdk.UntagResourceInput{
		ResourceArn: &resourceARN,
		TagKeys:     tagKeys,
	}
	_, err = rm.sdkapi.UntagResource(ctx, input)
	rm.metrics.RecordAPICall("UPDATE", "UntagResource", err)
	return err
}
